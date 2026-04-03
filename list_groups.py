"""სწრაფი სკრიპტი — ყველა ჯგუფის სახელი და ID"""
import asyncio
from telethon import TelegramClient
from dotenv import load_dotenv
import os

load_dotenv()

client = TelegramClient("user_session", int(os.environ["TG_API_ID"]), os.environ["TG_API_HASH"])

async def main():
    await client.start(phone=os.environ["TG_PHONE"])
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f"{dialog.id:>20}  |  {dialog.name}")

asyncio.run(main())
