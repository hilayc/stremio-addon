# Private Telegram → Stremio

A single-container Stremio addon using a **Telegram user account**, FastAPI,
Telethon, and SQLite FTS5. It discovers joined private broadcast channels
(including archived dialogs), indexes uploaded videos and video documents,
and streams their bytes through your server.

## Start

1. Copy `.env.example` to `.env` and fill in your six settings.
2. Generate a random API key:
   ```sh
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
3. Generate a **Telethon** session on a trusted machine. This is separate from
   the unattended server and supports Telegram's login code and 2FA:
   ```sh
   python -m venv .venv
   . .venv/bin/activate
   pip install -r requirements.txt
   python generate_session.py
   ```
   Save its output in `.env` as `user_session_string`. Do not share that value.
   Use a dedicated session for this addon; do not run several containers with it.
4. Build and start:
   ```sh
   docker compose up --build -d
   ```
5. Put an HTTPS reverse proxy in front of `127.0.0.1:8000`. Set `addon_url`
   to that external base URL. For example, with Caddy installed on the host:
   ```caddyfile
   telegram.example.com {
       reverse_proxy 127.0.0.1:8000
   }
   ```
   DNS must point to your host and ports 80/443 must reach Caddy. Do not enable
   access logging containing full URLs. Disable proxy caching and response
   buffering; allow long streaming requests and forward Range headers.
6. In Stremio's addon installation field, paste:
   ```text
   https://telegram.example.com/YOUR_API_KEY/manifest.json
   ```
   The protected `/<api_key>/status` endpoint shows connection state, channel
   count, indexed videos, and persisted history checkpoints. Initial indexing
   runs in the background; search results are partial until it completes.

The Compose port is deliberately bound to localhost for a host reverse proxy.
If your reverse proxy is another container, join both to a Docker network and
proxy to `addon:8000`. The included Compose mapping uses lowercase `port`.

## Configuration

| Environment variable | Meaning |
|---|---|
| `port` | Listener port; default `8000` |
| `addon_url` | External HTTPS base URL, optionally with a path prefix |
| `debug_enabled` | Enable the read-only debug dashboard (default `true`) |
| `debug_port` | Separate dashboard port (default `8001`) |
| `debug_host` | Dashboard listen address (default `0.0.0.0`) |
| `api_key` | At least 32 URL-safe letters, digits, `_` or `-` |
| `api_id` | Telegram application ID from https://my.telegram.org |
| `api_hash` | Telegram application hash |
| `user_session_string` | Authorized Telethon StringSession |
| `cache_mb` | Optional disk chunk-cache ceiling; default `512`, `0` disables writes |
| `data_dir` | Optional data directory; default `/data` |
| `CHANNEL_IDS` | Optional comma-separated negative channel IDs, e.g. `-1001234567890,-1009876543210` |

Set `CHANNEL_IDS` to scan only the listed joined private broadcast channels.
Omit it or leave it blank to scan all joined private broadcast channels as before.
Spaces around IDs and duplicate IDs are accepted; malformed lists fail startup.
Use the full negative IDs shown in `/status` checkpoints. Restart after changing
the list. Previously indexed uploads from excluded channels disappear from the
catalog at discovery; Telegram posts are untouched. Re-including a channel
starts its history scan again. This option does not join channels or include
public channels or groups.

### Debug dashboard

With `debug_enabled=true`, open `http://HOST:8001` (or the configured
`debug_port`) and sign in with the add-on API key. The dashboard shows Telegram
connection and indexing state, every channel included in queries, recent addon
activity, and read-only text or IMDb searches. Search results expose diagnostic
metadata and matching reasons, but the debug server does not register playback,
thumbnail, mapping, or download routes and never returns signed media URLs.

The dashboard always calls relative `/api/...` paths on its own listener. The
`addon_url` setting is used only by the Stremio API when it creates stream and
thumbnail URLs.

The app accepts uppercase equivalents, with lowercase taking precedence.
Under Home Assistant, these settings are also read directly from
`/data/options.json`, and persistent application data defaults to
`/data/stremio`.
Only correctly spelled variables are used. The named volume preserves the index
and cache. One process/worker owns the client and database. Do not scale replicas
against the same data directory. The image runs as UID 10001; bind mounts need
appropriate ownership. Changing `port` also requires updating proxy configuration.

## Search and source matching

- Browse `Telegram Videos` or use Stremio search. Filenames, full captions, and
  derived titles are indexed. All uploads, including episodes, appear as
  standalone playable movie-type entries in this catalog.
- Hebrew and English display text is preserved. Search removes niqqud,
  cantillation and invisible direction marks, normalizes quotes/punctuation,
  and supports mixed languages and prefix terms without stripping Hebrew prefixes.
- Season/episode parsing supports `S02E05` and `עונה 2 פרק 5`.
- Existing Stremio movie/series pages request IMDb IDs. Caption/filename IMDb IDs
  and manual mappings take precedence. Otherwise Cinemeta supplies a title/year,
  and Wikidata supplies English/Hebrew labels and aliases. These require no API key.
- Movie inference requires an exact normalized title alias and matching year.
  Episodes require matching show title, season, and episode. Ambiguous/fuzzy
  guesses are intentionally omitted. Missing metadata leaves local browsing,
  search, and explicit mappings usable. Translated titles only match when the
  provider has the corresponding alias; normalization cannot translate titles.
- External requests contain only public IMDb identifiers, never your captions,
  filenames, session, channel IDs, or API key. Results are cached in memory for
  24 hours (failures for one minute). Providers can throttle or be unavailable.

Correct an indexed item using its `tg:<channel_id>:<message_id>` ID:

```sh
curl -X PUT "https://telegram.example.com/YOUR_API_KEY/mapping/tg:-1001234567890:123" \
  -H 'Content-Type: application/json' \
  --data '{"imdb":"tt0133093"}'
```

An episode mapping uses the **show's** IMDb ID; the upload must also have a
parseable season/episode. To change a mapping, PUT a replacement. Mappings
persist across reindexing. If a caption includes multiple IMDb IDs, the first
is used; use a manual correction when needed.

## Playback and indexing behavior

- Signed, file-scoped playback and thumbnail URLs expire after 24 hours. Reopen
  the source in Stremio to obtain a fresh URL. Rotating `api_key` invalidates them.
- GET/HEAD, full responses, single closed/open/suffix byte ranges, 206 and 416
  are supported. Multiple ranges are rejected with 416.
- Telegram reads are aligned to 512 KiB; exact requested bytes are sliced from
  bounded chunks. Four chunk reads may run concurrently. A disk LRU cache is
  bounded by `cache_mb`; cancellation closes download iterators. Expired file
  references are refreshed, and document IDs prevent reuse after media changes.
- No transcoding: codec/container support depends on the Stremio device.
  Every video byte travels through your server. Browser playback may require
  Stremio's streaming service; native clients are the intended first target.
- New/edited/deleted message events update the index. History advances in
  persisted batches; catch-up handles posts since the last scan. Discovery runs
  every five minutes. A rotating 100-message reconciliation checks old edits and
  deletions, so large libraries take multiple cycles to reconcile completely.
- Channel access loss removes indexed entries when detected; playback rechecks
  the original message before serving cached bytes. Revoked sessions appear in
  status and need a replacement plus container restart. Flood waits are respected.
- `/healthz` only reports that HTTP is running. Use the protected status endpoint
  for actual Telegram readiness. Initial session authorization failure stops startup.

## Security and deployment

The installation URL is a bearer credential granting access to private catalog
metadata and playback. Keep it private. The session gives access to your Telegram
account and stays on the server. API docs and application access logs are disabled;
private responses use `Cache-Control: private, no-store`. Protect your proxy logs,
`.env`, `/data`, and backups. Local cached chunks are ordinary unencrypted media
files. Use encrypted host storage if required; deleting a post removes its index
entry but its cached bytes remain until eviction or manual cache cleanup.

For a URL prefix such as `https://example.com/telegram`, configure the reverse
proxy to strip `/telegram` before forwarding. TLS terminates at the proxy.
No Telegram bot, channel invitations, posting, or account modifications are used.

## Tests and live acceptance

```sh
pip install -r requirements-dev.txt
python -m pytest -q
```

The automated suite uses a simulated Telegram backend: Hebrew normalization,
FTS updates/deletes, authentication, signed token scope/expiry, metadata matching,
HTTP ranges/HEAD, aligned downloads, and bounded cache behavior. It requires no
Telegram account. See `TESTING.md` for the live acceptance checklist.

## Protocol references

- [Stremio HTTP protocol](https://github.com/Stremio/stremio-addon-sdk/blob/master/docs/protocol.md)
- [Telethon client and download API](https://docs.telethon.dev/en/stable/modules/client.html)
- [Wikidata query service](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service)
