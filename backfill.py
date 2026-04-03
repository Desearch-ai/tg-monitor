"""
One-shot backfill: fetch last 7 days from nerds group and upsert into DB.
"""
import asyncio
import sqlite3
import os
import sys
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import Channel, Chat, User, MessageReplyHeader

load_dotenv()

API_ID    = int(os.environ["TG_API_ID"])
API_HASH  = os.environ["TG_API_HASH"]
PHONE     = os.environ["TG_PHONE"]
DB_PATH   = os.environ.get("DB_PATH", "monitor.db")
SESSION   = os.environ.get("SESSION_FILE", "user_session")

DIALOG_ID = -1002564889965
DAYS_BACK = 7
BATCH     = 200  # messages per iter_messages call

client = TelegramClient(SESSION, API_ID, API_HASH)

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            dialog_id   TEXT,
            dialog_name TEXT,
            dialog_type TEXT,
            msg_id      INTEGER,
            sender_id   TEXT,
            sender_name TEXT,
            text        TEXT,
            date        TEXT,
            reply_to_id INTEGER,
            UNIQUE(dialog_id, msg_id)
        )
    """)
    conn.commit()
    conn.close()

def upsert_messages(rows):
    conn = sqlite3.connect(DB_PATH)
    inserted = 0
    for r in rows:
        try:
            conn.execute(
                "INSERT OR IGNORE INTO messages "
                "(dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                r
            )
            if conn.total_changes > inserted:
                inserted += 1
        except Exception as e:
            print(f"  insert error: {e}")
    conn.commit()
    total = conn.execute("SELECT COUNT(*) FROM messages WHERE dialog_id=?", (str(DIALOG_ID),)).fetchone()[0]
    conn.close()
    return inserted, total

async def backfill():
    await client.start(phone=PHONE)
    print("✅ Telegram connected")

    cutoff = datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)
    entity = await client.get_entity(DIALOG_ID)
    dialog_name = getattr(entity, 'title', str(DIALOG_ID))
    print(f"📥 Fetching from: {dialog_name}")
    print(f"   Since: {cutoff.isoformat()}")

    total_fetched = 0
    total_inserted = 0
    batch_rows = []

    async for msg in client.iter_messages(entity, limit=None, offset_date=None, reverse=False):
        if msg.date < cutoff:
            break

        sender_id   = str(msg.sender_id) if msg.sender_id else ""
        sender_name = ""
        try:
            sender = await client.get_entity(msg.sender_id)
            if hasattr(sender, 'first_name'):
                sender_name = (sender.first_name or "") + (" " + sender.last_name if sender.last_name else "")
            elif hasattr(sender, 'title'):
                sender_name = sender.title
        except Exception:
            sender_name = sender_id

        reply_to = None
        if msg.reply_to and isinstance(msg.reply_to, MessageReplyHeader):
            reply_to = msg.reply_to.reply_to_msg_id

        batch_rows.append((
            str(DIALOG_ID),
            dialog_name,
            "group",
            msg.id,
            sender_id,
            sender_name.strip(),
            msg.text or "",
            msg.date.isoformat(),
            reply_to,
        ))
        total_fetched += 1

        if len(batch_rows) >= BATCH:
            ins, tot = upsert_messages(batch_rows)
            total_inserted += ins
            print(f"  batch: fetched {total_fetched}, inserted {ins}, DB total {tot}")
            batch_rows = []

    if batch_rows:
        ins, tot = upsert_messages(batch_rows)
        total_inserted += ins
        print(f"  final batch: fetched {total_fetched}, inserted {ins}, DB total {tot}")

    print(f"\n✅ Done. Fetched {total_fetched}, new rows inserted: {total_inserted}")
    await client.disconnect()

if __name__ == "__main__":
    init_db()
    asyncio.run(backfill())
