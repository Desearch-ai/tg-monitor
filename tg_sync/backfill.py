"""Bounded historical Telegram read/backfill helpers."""

from __future__ import annotations

import asyncio
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


from .accounts import Account
from .config import MAX_LIMIT

MAX_BACKFILL_LIMIT = min(1000, MAX_LIMIT)


@dataclass(frozen=True)
class BackfillRequest:
    dialog: str
    limit: int
    before_id: int | None = None
    before_date: str | None = None
    dry_run: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "dialog": self.dialog,
            "limit": self.limit,
            "before_id": self.before_id,
            "before_date": self.before_date,
        }


def build_backfill_request(dialog: str | None, limit: int | str | None, before_id: int | str | None = None, before_date: str | None = None, dry_run: bool = False) -> BackfillRequest:
    if not dialog:
        raise ValueError("--dialog is required")
    bounded = int(limit) if limit is not None else 100
    if bounded < 1:
        raise ValueError("limit must be >= 1")
    if bounded > MAX_BACKFILL_LIMIT:
        raise ValueError(f"limit must be <= {MAX_BACKFILL_LIMIT}")
    parsed_before_id = int(before_id) if before_id is not None else None
    if parsed_before_id is not None and parsed_before_id < 1:
        raise ValueError("before-id must be >= 1")
    if before_date:
        _parse_datetime(before_date)
    return BackfillRequest(str(dialog), bounded, parsed_before_id, before_date, dry_run)


def dry_run_plan(account: Account, request: BackfillRequest) -> dict[str, Any]:
    return {
        "ok": True,
        "dry_run": True,
        "account": account.to_json(),
        "request": request.to_json(),
        "db_path": str(account.db_path),
        "session_path": account.session_path,
        "telegram_writes": "forbidden",
        "would_read": "TelegramClient.get_messages",
        "would_write_local_db": True,
    }


def run_backfill(account: Account, request: BackfillRequest) -> dict[str, Any]:
    if request.dry_run:
        return dry_run_plan(account, request)
    return asyncio.run(_run_backfill_async(account, request))


async def _run_backfill_async(account: Account, request: BackfillRequest) -> dict[str, Any]:
    api_id = int(os.environ["TG_API_ID"])
    api_hash = os.environ["TG_API_HASH"]
    phone = os.environ.get("TG_PHONE")
    from telethon import TelegramClient

    client = TelegramClient(account.session_path, api_id, api_hash)
    await client.start(phone=phone)
    try:
        entity = await client.get_entity(int(request.dialog) if request.dialog.lstrip("-").isdigit() else request.dialog)
        kwargs: dict[str, Any] = {"limit": request.limit}
        if request.before_id:
            kwargs["max_id"] = request.before_id
        if request.before_date:
            kwargs["offset_date"] = _parse_datetime(request.before_date)
        raw = await client.get_messages(entity, **kwargs)
        messages = []
        for msg in raw:
            if not getattr(msg, "text", None):
                continue
            messages.append(await _message_to_row(msg))
        dialog_name = getattr(entity, "title", None) or getattr(entity, "first_name", None) or str(request.dialog)
        dialog_type = "channel" if getattr(entity, "broadcast", False) else "group" if getattr(entity, "megagroup", False) else "dm"
        saved = save_backfill_messages(account.db_path, account.id, request.dialog, dialog_name, dialog_type, messages)
        return {
            "ok": True,
            "dry_run": False,
            "account": account.to_json(),
            "request": request.to_json(),
            "fetched": len(messages),
            "saved": saved,
            "telegram_writes": "forbidden",
        }
    finally:
        await client.disconnect()


async def _message_to_row(msg: Any) -> dict[str, Any]:
    sender_id = 0
    sender_name = "Unknown"
    try:
        sender = await msg.get_sender()
        if sender:
            sender_id = int(getattr(sender, "id", 0) or 0)
            sender_name = getattr(sender, "first_name", None) or getattr(sender, "title", None) or "Unknown"
    except Exception:
        pass
    reply_to_id = None
    try:
        from telethon.tl.types import MessageReplyHeader
    except Exception:  # pragma: no cover - only when Telethon is unavailable in unit tests
        MessageReplyHeader = ()
    if isinstance(getattr(msg, "reply_to", None), MessageReplyHeader):
        reply_to_id = msg.reply_to.reply_to_msg_id
    return {
        "id": int(msg.id),
        "sender_id": sender_id,
        "sender": sender_name,
        "text": msg.text,
        "date": msg.date.isoformat() if getattr(msg, "date", None) else datetime.now(timezone.utc).isoformat(),
        "reply_to_id": reply_to_id,
    }


def save_backfill_messages(db_path: str | Path, account_id: str, dialog_id: str, dialog_name: str, dialog_type: str, messages: list[dict[str, Any]]) -> int:
    init_account_db(db_path)
    conn = sqlite3.connect(str(db_path))
    saved = 0
    try:
        for message in messages:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO messages
                    (account_id, dialog_id, dialog_name, dialog_type, msg_id, sender_id, sender_name, text, date, reply_to_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    account_id,
                    str(dialog_id),
                    dialog_name,
                    dialog_type,
                    message["id"],
                    message["sender_id"],
                    message["sender"],
                    message["text"],
                    message["date"],
                    message.get("reply_to_id"),
                ),
            )
            saved += cur.rowcount
        conn.commit()
    finally:
        conn.close()
    return saved


def init_account_db(db_path: str | Path) -> None:
    path = Path(db_path)
    if path.parent and str(path.parent) not in {"", "."}:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                account_id TEXT DEFAULT 'default',
                dialog_id TEXT,
                dialog_name TEXT,
                dialog_type TEXT,
                msg_id INTEGER,
                sender_id INTEGER,
                sender_name TEXT,
                text TEXT,
                date TEXT,
                reply_to_id INTEGER,
                UNIQUE(account_id, dialog_id, msg_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS last_seen (
                account_id TEXT DEFAULT 'default',
                dialog_id TEXT,
                message_id INTEGER DEFAULT 0,
                PRIMARY KEY(account_id, dialog_id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
