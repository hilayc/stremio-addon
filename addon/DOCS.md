# Stremio Telegram for Home Assistant

This folder describes an amd64 Home Assistant add-on for the program in the
repository root. It uses `ghcr.io/home-assistant/amd64-base:3.24` and runs the
published image `ghcr.io/hilayc/stremio-addon:main`.

Home Assistant appends `version` to `image`, so `config.yaml` intentionally has
`image: ghcr.io/hilayc/stremio-addon` and `version: main`. Putting `:main` inside
the image field would cause Home Assistant to append a second tag.

## Build and publish

From the **repository root**, run:

```sh
docker build --platform linux/amd64 \
  --build-arg BUILD_FROM=ghcr.io/home-assistant/amd64-base:3.24 \
  -f homeassistant-addon/Dockerfile \
  -t ghcr.io/hilayc/stremio-addon:main .
docker push ghcr.io/hilayc/stremio-addon:main
```

The final dot is important: the Dockerfile copies the existing `addon/`,
`requirements.txt`, and `generate_session.py` from the repository root.
Build automation must use `context: .` and
`file: homeassistant-addon/Dockerfile`. It must build this HA-specific image,
not the repository's original standalone Dockerfile, for the `main` tag.

Make the GHCR package public so Supervisor can pull it. Publishing is a separate
step; these files do not upload an image automatically. With a mutable `main`
tag, future pushes do not produce a new HA version notification; reinstall to
pull the replacement (back up the add-on first), or adopt versioned tags later.

## Install

After pushing these files to the repository and publishing the image, add
`https://github.com/hilayc/stremio-addon` in Home Assistant's add-on store
repository menu. Install **Stremio Telegram** on an amd64 installation.

Alternatively, copy this entire folder to `/addons/homeassistant-addon` on
Home Assistant, reload the add-on store, and install the local entry. It still
pulls the published image. Local Supervisor source builds are not supported by
this folder alone: source builds require the repository-root context above.

## Configure

Set these options in the add-on's Configuration tab:

| Option / environment variable | Value |
| --- | --- |
| `port` | Unused host TCP port, default `8000` |
| `addon_url` | External HTTPS base URL of your reverse proxy |
| `api_key` | At least 32 random URL-safe characters |
| `api_id` | Positive Telegram application ID |
| `api_hash` | Telegram application hash |
| `user_session_string` | Complete Telethon StringSession |
| `cache_mb` | Optional cache limit in MiB, default `512` |

All six requested environment variables are exported with their exact lowercase
names. Home Assistant options take precedence over environment values. When
running this image outside HA without `/data/options.json`, the program's
normal lowercase/uppercase environment variables still work.

Generate the session on a trusted machine using the repository's
`generate_session.py`. A Pyrogram/GramJS session or a `.session` filename is not
a Telethon StringSession. Startup validates the session format and reports a
clear error without printing the value. Telegram authorization is then checked
by the application.

Host networking lets the configured `port` work without a fixed Docker port
mapping. The server listens on all host interfaces, so choose a free port and
configure your network accordingly. No Supervisor or Home Assistant API access
is requested. Ingress is not used: Stremio needs a directly reachable endpoint.

Point your HTTPS reverse proxy at `http://HOME_ASSISTANT_IP:8000` (or your
configured port). Forward byte-range requests and disable proxy caching and
access logs that expose credential-bearing URLs. Install in Stremio with:

```text
https://YOUR_DOMAIN/YOUR_API_KEY/manifest.json
```

The protected `/<api_key>/status` endpoint shows Telegram indexing progress.
SQLite and video chunks persist under `/data/stremio`; Supervisor keeps options
separately in `/data/options.json`. Restart the add-on after changing options.

Reference: [Home Assistant add-on configuration](https://developers.home-assistant.io/docs/add-ons/configuration/).
