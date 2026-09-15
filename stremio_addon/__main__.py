import logging
import uvicorn
from .core import Settings

if __name__ == '__main__':
    settings = Settings.env()
    # No request paths or Telegram payloads in application logs.
    logging.getLogger('telethon').setLevel(logging.CRITICAL)
    uvicorn.run('addon.app:app', host='0.0.0.0', port=settings.port, workers=1, access_log=False)
