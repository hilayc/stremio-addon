import asyncio
import hmac
import json
import logging
import re
import time
import unicodedata
from contextlib import asynccontextmanager
from urllib.parse import parse_qs
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel
from .core import Settings, Store, Tokens, byte_range
from .metadata import Metadata
from .telegram import Telegram

interaction_log = logging.getLogger('uvicorn.error.interactions')
interaction_log.setLevel(logging.INFO)


def safe_log_text(value, cfg):
    text = str(value)
    for secret in (cfg.key, cfg.session, cfg.api_hash):
        if secret:
            text = text.replace(secret, '[redacted]')
    text = re.sub(r'https?://\S+|/(?:play|thumb)/\S+', '[url]', text)
    text = ''.join(' ' if unicodedata.category(c).startswith('C') else c for c in text)
    return text[:200]


def create_app(settings=None, gateway_factory=Telegram):
    @asynccontextmanager
    async def lifespan(app):
        cfg = settings or Settings.env()
        cfg.data.mkdir(parents=True, exist_ok=True)
        store = Store(cfg.data / 'index.sqlite3')
        gateway = gateway_factory(cfg, store)
        metadata = Metadata()
        app.state.cfg, app.state.store, app.state.tg = cfg, store, gateway
        app.state.tokens, app.state.metadata = Tokens(cfg.key), metadata
        try:
            await gateway.start()
            yield
        finally:
            await gateway.close()
            await metadata.http.aclose()
            store.db.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(CORSMiddleware, allow_origins=['*'], allow_methods=['GET', 'HEAD', 'OPTIONS', 'PUT'], allow_headers=['Range', 'Content-Type'], expose_headers=['Content-Range', 'Content-Length', 'Accept-Ranges'])

    @app.middleware('http')
    async def private(request, call_next):
        started = time.perf_counter()
        status_code = 500
        error = None
        try:
            response = await call_next(request)
            status_code = response.status_code
        except Exception as exc:
            error = type(exc).__name__
            raise
        finally:
            summary = getattr(request.state, 'interaction', None)
            if summary is not None:
                summary.update(status=status_code, duration_ms=round((time.perf_counter() - started) * 1000, 1))
                if error:
                    summary['error'] = error
                interaction_log.log(logging.WARNING if status_code >= 400 else logging.INFO,
                                    '%s', json.dumps(summary, ensure_ascii=False))
        response.headers['Cache-Control'] = 'private, no-store'
        response.headers['Referrer-Policy'] = 'no-referrer'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        return response

    def interaction(request, event, **fields):
        request.state.interaction = dict(event=event, **{
            name: safe_log_text(value, app.state.cfg) if isinstance(value, str) else value
            for name, value in fields.items()
        })

    def results(request, rows):
        request.state.interaction.update(
            result_count=len(rows),
            channel_count=len({r['channel'] for r in rows}),
            sample_titles=[safe_log_text(r['title'], app.state.cfg) for r in rows[:3]],
        )

    def auth(key):
        if not hmac.compare_digest(key.encode(), app.state.cfg.key.encode()):
            raise HTTPException(404)

    def token_row(token, scope):
        try:
            item = app.state.tokens.verify(token, scope)
        except ValueError:
            raise HTTPException(403, 'Invalid or expired token') from None
        row = app.state.store.get(item)
        if not row or row['channel'] not in app.state.tg.channels:
            raise HTTPException(404)
        return row

    def url(row, scope):
        return f'{app.state.cfg.url}/{scope}/{app.state.tokens.sign(row["id"], scope)}'

    def meta(row):
        return {'id': row['id'], 'type': 'movie', 'name': row['title'], 'posterShape': 'landscape', 'poster': url(row, 'thumb'), 'description': row['caption'] + '\n\n' + row['channel_name'], 'releaseInfo': str(row['year'] or ''), 'behaviorHints': {'defaultVideoId': row['id']}}

    @app.get('/healthz')
    async def health():
        return {'ok': True}

    @app.get('/{key}/manifest.json')
    async def manifest(key):
        auth(key)
        return {'id': 'community.private.telegram', 'version': '1.0.0', 'name': 'Private Telegram Videos',
                'description': 'Your private Telegram videos with Hebrew and English search.', 'types': ['movie', 'series'],
                'resources': [{'name': 'catalog', 'types': ['movie']}, {'name': 'meta', 'types': ['movie'], 'idPrefixes': ['tg:']}, {'name': 'stream', 'types': ['movie', 'series'], 'idPrefixes': ['tg:', 'tt']}],
                'catalogs': [{'type': 'movie', 'id': 'telegram', 'name': 'Telegram Videos', 'extra': [{'name': 'search', 'isRequired': False}, {'name': 'skip', 'isRequired': False}]}]}

    @app.get('/{key}/catalog/{kind}/{catalog_id}.json')
    @app.get('/{key}/catalog/{kind}/{catalog_id}/{extras}.json')
    async def catalog(key, kind, catalog_id, request: Request, extras=''):
        auth(key)
        args = parse_qs(extras)
        query = args.get('search', [''])[0][:500]
        interaction(request, 'catalog_search' if query else 'catalog_browse', query=query,
                    type=kind, catalog=catalog_id, result_count=0)
        if kind != 'movie' or catalog_id != 'telegram':
            return {'metas': []}
        try:
            skip = int(args.get('skip', ['0'])[0])
            if skip < 0 or skip > 10_000_000:
                raise ValueError()
        except ValueError:
            raise HTTPException(400, 'Invalid skip') from None
        request.state.interaction['skip'] = skip
        rows = [r for r in app.state.store.catalog(query, skip) if r['channel'] in app.state.tg.channels]
        results(request, rows)
        return {'metas': [meta(r) for r in rows]}

    @app.get('/{key}/meta/{kind}/{item}.json')
    async def detail(key, kind, item, request: Request):
        auth(key)
        interaction(request, 'meta_lookup', type=kind, item=item)
        row = app.state.store.get(item)
        rows = [row] if kind == 'movie' and row and row['channel'] in app.state.tg.channels else []
        results(request, rows)
        return {'meta': meta(row) if rows else None}

    @app.get('/{key}/stream/{kind}/{item}.json')
    async def sources(key, kind, item, request: Request):
        auth(key)
        interaction(request, 'stream_lookup', type=kind, item=item,
                    match_mode='telegram_id' if item.startswith('tg:') else 'metadata', result_count=0)
        if kind not in ('movie', 'series'):
            return {'streams': []}
        if item.startswith('tg:'):
            row = app.state.store.get(item)
            rows = [row] if row and kind == 'movie' else []
        else:
            rows = await app.state.metadata.match(app.state.store, kind, item)
        rows = [r for r in rows if r['channel'] in app.state.tg.channels]
        results(request, rows)
        return {'streams': [{'name': 'Telegram ' + r['quality'], 'title': f"{r['title']}\n{r['channel_name']} · {r['size'] / 1024**3:.2f} GB", 'url': url(r, 'play'), 'behaviorHints': {'notWebReady': True}} for r in rows if r['channel'] in app.state.tg.channels]}

    @app.get('/{key}/status')
    async def status(key):
        auth(key)
        return {**app.state.tg.status, 'connected': app.state.tg.client.is_connected(), 'channels': len(app.state.tg.channels), 'videos': app.state.store.db.execute('SELECT count(*) FROM videos').fetchone()[0], 'checkpoints': [dict(r) for r in app.state.store.db.execute('SELECT * FROM checkpoints')]}

    class Mapping(BaseModel):
        imdb: str

    @app.put('/{key}/mapping/{item}')
    async def mapping(key, item, body: Mapping):
        auth(key)
        if not app.state.store.get(item) or not re.fullmatch(r'tt\d{7,10}', body.imdb):
            raise HTTPException(400, 'Expected indexed Telegram ID and IMDb ID')
        with app.state.store.db:
            app.state.store.db.execute('INSERT OR REPLACE INTO mappings VALUES (?,?)', (item, body.imdb))
        return {'ok': True}

    @app.api_route('/play/{token}', methods=['GET', 'HEAD'])
    async def play(token, request: Request):
        row = token_row(token, 'play')
        try:
            message = await app.state.tg.message(row)
        except FileNotFoundError:
            raise HTTPException(404) from None
        row = app.state.store.get(row['id'])
        size = message.document.size
        try:
            start, end, code = byte_range(request.headers.get('range'), size)
        except ValueError:
            return Response(status_code=416, headers={'Content-Range': f'bytes */{size}', 'Accept-Ranges': 'bytes'})
        headers = {'Accept-Ranges': 'bytes', 'Content-Length': str(max(0, end - start + 1))}
        if code == 206:
            headers['Content-Range'] = f'bytes {start}-{end}/{size}'
        if request.method == 'HEAD' or size == 0:
            return Response(status_code=code, headers=headers, media_type=row['mime'])
        iterator = app.state.tg.stream(row, message, start, end)
        # Fetch first chunk before sending success headers, allowing a clean upstream error.
        try:
            first = await anext(iterator)
        except Exception:
            await iterator.aclose()
            raise HTTPException(502, 'Telegram download unavailable') from None
        async def body():
            try:
                yield first
                async for chunk in iterator:
                    yield chunk
            finally:
                await iterator.aclose()
        return StreamingResponse(body(), status_code=code, headers=headers, media_type=row['mime'])

    @app.get('/thumb/{token}')
    async def thumb(token):
        row = token_row(token, 'thumb')
        try:
            message = await app.state.tg.message(row)
            data = await app.state.tg.client.download_media(message, file=bytes, thumb=-1)
            if data:
                return Response(data, media_type='image/jpeg')
        except FileNotFoundError:
            raise HTTPException(404) from None
        except Exception:
            pass
        return Response('<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360" fill="#182936"/><path d="M280 120v120l100-60z" fill="#64b5f6"/></svg>', media_type='image/svg+xml')

    return app

app = create_app()
