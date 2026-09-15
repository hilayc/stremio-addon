from types import SimpleNamespace
from urllib.parse import quote
import pytest
from fastapi.testclient import TestClient
from addon.core import Settings, Store, Tokens, byte_range, normalize, parse_title
from addon.app import create_app
from addon.metadata import Metadata
from addon.telegram import Telegram, CHUNK


def row(**updates):
    result = dict(id='tg:-100123:7', channel=-100123, message=7, title='שם הסרט', filename='שם הסרט 2024.mp4', caption='כיתוב בעברית', channel_name='ערוץ', size=10, mime='video/mp4', date=1, year=2024, season=None, episode=None, imdb=None, quality='1080p', file_id='99')
    result.update(updates)
    return result


@pytest.mark.parametrize('value,expected', [(None,(0,9,200)),('bytes=2-5',(2,5,206)),('bytes=7-',(7,9,206)),('bytes=-3',(7,9,206)),('bytes=-99',(0,9,206)),('bytes=0-99',(0,9,206))])
def test_ranges(value, expected):
    assert byte_range(value, 10) == expected


@pytest.mark.parametrize('value', ['bytes=10-', 'bytes=7-2', 'bytes=-0', 'bytes=-', 'bytes=1-2,4-5', 'bad'])
def test_invalid_ranges(value):
    with pytest.raises(ValueError):
        byte_range(value, 10)


def test_hebrew():
    assert normalize('\u200fשָׁלוֹם־עוֹלָם') == normalize('שלום עולם')
    assert normalize('צה״ל') == normalize('צה"ל')
    p = parse_title('שם.הסדרה עונה 2 פרק 5 1080p.mkv', '')
    assert (p['title'], p['season'], p['episode']) == ('שם הסדרה', 2, 5)
    assert parse_title('Show.S02E05.mkv', '')['episode'] == 5


def test_store(tmp_path):
    db = Store(tmp_path / 'db')
    db.upsert(row())
    assert db.catalog('שֵׁם')[0]['title'] == 'שם הסרט'
    assert db.catalog('כיתוב')
    assert not db.catalog('" OR *')
    db.upsert(row(caption='חדש'))
    assert not db.catalog('כיתוב')
    assert db.catalog('חדש')
    db.delete('tg:-100123:7')
    assert not db.catalog()
    db.db.close()


def test_tokens():
    t = Tokens('a' * 32)
    token = t.sign('tg:-100123:7', 'play')
    assert t.verify(token, 'play') == 'tg:-100123:7'
    for value, scope in [(token + 'x', 'play'), (token, 'thumb'), (t.sign('tg:-100123:7', 'play', -1), 'play')]:
        with pytest.raises(ValueError):
            t.verify(value, scope)


def test_home_assistant_options_fallback(tmp_path, monkeypatch):
    import addon.core as core
    options = tmp_path / 'options.json'
    options.write_text('''{
      "port": 9123,
      "addon_url": "https://telegram.example.com",
      "api_key": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "api_id": 12345,
      "api_hash": "hash",
      "user_session_string": "session",
      "cache_mb": 42
    }''')
    real_path = core.Path
    monkeypatch.setattr(core, 'Path', lambda value: options if value == '/data/options.json' else real_path(value))
    for name in ('port', 'addon_url', 'api_key', 'api_id', 'api_hash', 'user_session_string', 'cache_mb',
                 'PORT', 'ADDON_URL', 'API_KEY', 'API_ID', 'API_HASH', 'USER_SESSION_STRING', 'CACHE_MB'):
        monkeypatch.delenv(name, raising=False)
    settings = core.Settings.env()
    assert settings.port == 9123
    assert settings.url == 'https://telegram.example.com'
    assert settings.api_id == 12345
    assert settings.cache_bytes == 42 * 1024**2
    assert settings.data == real_path('/data/stremio')


def test_environment_overrides_home_assistant_options(tmp_path, monkeypatch):
    import addon.core as core
    options = tmp_path / 'options.json'
    options.write_text('{"addon_url":"https://ha.example.com"}')
    real_path = core.Path
    monkeypatch.setattr(core, 'Path', lambda value: options if value == '/data/options.json' else real_path(value))
    monkeypatch.setenv('addon_url', 'https://environment.example.com')
    monkeypatch.setenv('api_key', 'a' * 32)
    monkeypatch.setenv('api_id', '123')
    monkeypatch.setenv('api_hash', 'hash')
    monkeypatch.setenv('user_session_string', 'session')
    assert core.Settings.env().url == 'https://environment.example.com'


class FakeTelegram:
    def __init__(self, cfg, store):
        self.store = store
        self.channels = {-100123: 'channel'}
        self.client = SimpleNamespace(is_connected=lambda: True)
        self.status = {'phase': 'ready'}
    async def start(self):
        self.store.upsert(row())
    async def close(self):
        pass
    async def message(self, row):
        return SimpleNamespace(document=SimpleNamespace(size=10))
    async def stream(self, row, message, start, end):
        yield b'0123456789'[start:end + 1]


def test_http(tmp_path):
    key = 'a' * 32
    cfg = Settings(8000, 'https://example.com', key, 1, 'hash', 'session', tmp_path)
    app = create_app(cfg, FakeTelegram)
    with TestClient(app) as client:
        assert client.get('/wrong/manifest.json').status_code == 404
        assert client.get('/docs').status_code == 404
        r = client.get(f'/{key}/manifest.json', headers={'Origin':'https://web.stremio.com'})
        assert r.headers['access-control-allow-origin'] == '*'
        assert r.headers['cache-control'] == 'private, no-store'
        assert r.json()['catalogs'][0]['extra'][0]['name'] == 'search'
        assert len(client.get(f'/{key}/catalog/movie/telegram/search={quote("שֵׁם")}.json').json()['metas']) == 1
        assert client.get(f'/{key}/catalog/movie/telegram/skip=-1.json').status_code == 400
        stream = client.get(f'/{key}/stream/movie/tg:-100123:7.json').json()['streams'][0]['url']
        path = stream.removeprefix('https://example.com')
        assert client.get(path).content == b'0123456789'
        r = client.get(path, headers={'Range': 'bytes=2-5'})
        assert r.status_code == 206 and r.content == b'2345'
        assert r.headers['content-range'] == 'bytes 2-5/10'
        assert client.head(path, headers={'Range':'bytes=-3'}).headers['content-length'] == '3'
        assert client.head(path).content == b''
        assert client.get(path, headers={'Range':'bytes=99-'}).status_code == 416
        assert client.get(path + 'x').status_code == 403
        thumb = client.get(f'/{key}/meta/movie/tg:-100123:7.json').json()['meta']['poster']
        assert client.get(thumb.replace('/thumb/', '/play/')).status_code == 403
        assert client.put(f'/{key}/mapping/tg:-100123:7', json={'imdb':'tt1234567'}).status_code == 200
        app.state.tg.channels.clear()
        assert client.get(path).status_code == 404
        assert client.get(f'/{key}/catalog/movie/telegram.json').json()['metas'] == []


@pytest.mark.asyncio
async def test_matching(tmp_path):
    store = Store(tmp_path / 'db')
    store.upsert(row())
    store.upsert(row(id='tg:-100123:8', message=8, year=2023))
    metadata = Metadata()
    async def resolve(kind, imdb):
        return ['English Title', 'שם הסרט'], 2024
    metadata.resolve = resolve
    assert [r['message'] for r in await metadata.match(store, 'movie', 'tt1234567')] == [7]
    store.upsert(row(imdb='tt7654321'))
    assert not await metadata.match(store, 'movie', 'tt1234567')
    store.upsert(row(season=2, episode=5, imdb='tt1234567'))
    assert await metadata.match(store, 'series', 'tt1234567:2:5')
    assert not await metadata.match(store, 'series', 'tt1234567:2:6')
    assert not await metadata.match(store, 'movie', 'tt1234567:2:5')
    await metadata.http.aclose()
    store.db.close()


@pytest.mark.asyncio
async def test_chunk_alignment_and_cache(tmp_path):
    cfg = Settings(8000, 'http://localhost', 'a'*32, 1, 'hash', '', tmp_path, CHUNK)
    store = Store(tmp_path / 'db')
    gateway = Telegram(cfg, store)
    payload = bytes(range(256)) * 5000
    calls = []
    class Iterator:
        async def __anext__(self):
            return payload[self.offset:self.offset+CHUNK]
        async def close(self):
            pass
    class Client:
        def iter_download(self, message, **kwargs):
            assert kwargs['offset'] % CHUNK == 0
            calls.append(kwargs['offset'])
            result = Iterator()
            result.offset = kwargs['offset']
            return result
    gateway.client = Client()
    message = SimpleNamespace(document=SimpleNamespace(id=99, size=len(payload)))
    start, end = CHUNK-13, CHUNK+21
    result = b''.join([x async for x in gateway.stream(row(), message, start, end)])
    assert result == payload[start:end+1]
    assert calls == [0, CHUNK]
    assert sum(p.stat().st_size for p in gateway.cache.iterdir()) <= CHUNK
    await gateway.chunk(row(), message, CHUNK)
    assert calls == [0, CHUNK]
    store.db.close()

@pytest.mark.asyncio
async def test_expired_reference_and_cancellation(tmp_path):
    import asyncio
    from telethon import errors
    cfg = Settings(8000, 'http://localhost', 'a'*32, 1, 'hash', '', tmp_path, 0)
    store = Store(tmp_path / 'db')
    gateway = Telegram(cfg, store)
    message = SimpleNamespace(document=SimpleNamespace(id=99, size=10))
    closed, attempts, refreshed = [], [], []
    class Iterator:
        async def __anext__(self):
            if len(attempts) == 1:
                raise errors.FileReferenceExpiredError(request=None)
            return b'0123456789'
        async def close(self):
            closed.append(True)
    class Client:
        def iter_download(self, message, **kwargs):
            attempts.append(True)
            return Iterator()
    async def refresh(r):
        refreshed.append(True)
        return message
    gateway.client = Client()
    gateway.message = refresh
    assert await gateway.chunk(row(), message, 0) == b'0123456789'
    assert len(closed) == 2 and len(refreshed) == 1
    class CancelIterator(Iterator):
        async def __anext__(self):
            raise asyncio.CancelledError()
    gateway.client.iter_download = lambda *a, **kw: CancelIterator()
    with pytest.raises(asyncio.CancelledError):
        await gateway.chunk(row(), message, 0)
    assert len(closed) == 3
    store.db.close()


@pytest.mark.asyncio
async def test_history_restart_checkpoint(tmp_path):
    cfg = Settings(8000, 'http://localhost', 'a'*32, 1, 'hash', '', tmp_path, 0)
    store = Store(tmp_path / 'db')
    gateway = Telegram(cfg, store)
    messages = [SimpleNamespace(id=i) for i in range(205, 0, -1)]
    indexed = []
    gateway.index = lambda channel, message: indexed.append(message.id)
    class Client:
        async def get_messages(self, entity, limit, offset_id):
            return [m for m in messages if not offset_id or m.id < offset_id][:limit]
        async def iter_messages(self, entity, min_id, reverse):
            for message in sorted(messages, key=lambda m: m.id):
                if message.id > min_id:
                    yield message
    gateway.client = Client()
    await gateway.scan(123, None)
    assert store.checkpoint(123)['oldest'] == 106
    store.db.close()
    gateway.store = Store(tmp_path / 'db')
    messages.insert(0, SimpleNamespace(id=206))
    await gateway.scan(123, None)
    await gateway.scan(123, None)
    assert set(indexed) == set(range(1, 207))
    assert gateway.store.checkpoint(123) == dict(channel=123, oldest=1, newest=206, complete=1)
    gateway.store.db.close()
