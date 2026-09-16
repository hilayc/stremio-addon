"""Translate Supervisor options to the application's environment, without logging secrets."""
import json
import os
import sys
from pathlib import Path

FIELDS = (
    "port", "addon_url", "api_key", "api_id", "api_hash",
    "user_session_string", "cache_mb", "CHANNEL_IDS",
)


def configure(options, environ):
    for name in FIELDS:
        if name in options:
            environ[name] = str(options[name])
    # Supervisor owns /data/options.json; keep app files in their own directory.
    environ["data_dir"] = "/data/stremio"


def main():
    options_path = Path("/data/options.json")
    try:
        options = json.loads(options_path.read_text()) if options_path.exists() else {}
        if not isinstance(options, dict):
            raise ValueError()
    except (OSError, ValueError):
        sys.exit("Cannot read Home Assistant options: expected a JSON object in /data/options.json.")
    configure(options, os.environ)

    from addon.core import Settings
    from telethon.sessions import StringSession

    try:
        settings = Settings.env()
    except ValueError as exc:
        sys.exit(str(exc))
    try:
        session = StringSession(settings.session)
        if not session.auth_key:
            raise ValueError()
    except Exception:
        sys.exit(
            "Invalid user_session_string. Generate a Telethon StringSession using "
            "generate_session.py, then paste only its complete output value into "
            "the add-on's user_session_string option."
        )
    print(f"Starting Stremio Telegram on port {settings.port}", flush=True)
    os.execv(sys.executable, [sys.executable, "-m", "addon"])


if __name__ == "__main__":
    main()
