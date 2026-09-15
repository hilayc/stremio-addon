"""Run interactively on your own machine; never paste the output into chat."""
import asyncio
import getpass
from telethon import TelegramClient
from telethon.sessions import StringSession

async def main():
    api_id = int(input('Telegram API ID: '))
    api_hash = getpass.getpass('Telegram API hash: ')
    client = TelegramClient(StringSession(), api_id, api_hash)
    try:
        await client.start(phone=lambda: input('Phone number (international format): '), password=lambda: getpass.getpass('Telegram 2FA password: '))
        print('\nStore this value privately as user_session_string:\n')
        print(client.session.save())
    finally:
        await client.disconnect()

if __name__ == '__main__':
    asyncio.run(main())
