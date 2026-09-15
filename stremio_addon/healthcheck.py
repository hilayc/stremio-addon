"""Container health check using the same configuration source as the server."""
import sys
from urllib.error import URLError
from urllib.request import urlopen

from .core import Settings


def main():
    try:
        port = Settings.env().port
        with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=4) as response:
            if response.status != 200:
                raise RuntimeError()
    except (OSError, RuntimeError, URLError, ValueError):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
