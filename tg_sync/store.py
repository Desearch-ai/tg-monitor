"""Read-only SQLite access for Telegram Monitor data."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .config import clamp_limit, resolve_db_path


class DBUnavailable(RuntimeError):
    """Raised when the local monitor DB cannot be opened/read."""


def _readonly_uri(path: Path) -> str:
    resolved = str(path.expanduser().resolve())
    return f"file:{quote(resolved, safe='/:')}?mode=ro"


class ReadOnlyStore:
    def __init__(self, db_path: str | Path | None = None, account_id: str | None = None):
        self.db_path = resolve_db_path(str(db_path) if db_path else None)
        self.account_id = account_id

    def connect(self) -> sqlite3.Connection:
        if not self.db_path.exists():
            raise DBUnavailable(f"DB not found: {self.db_path}")
        try:
            conn = sqlite3.connect(_readonly_uri(self.db_path), uri=True)
            conn.row_factory = sqlite3.Row
            return conn
        except sqlite3.Error as exc:
            raise DBUnavailable(str(exc)) from exc

    def list_dialogs(
        self,
        dialog_type: str | None = None,
        limit: int = 50,
        query: str | None = None,
        min_count: int = 0,
        groups_only: bool = False,
    ) -> list[dict[str, Any]]:
        limit = clamp_limit(limit)
        with self.connect() as conn:
            has_account_id = self._has_account_id(conn)
            where: list[str] = []
            params: list[Any] = []
            account_sql, account_params = self._account_where(has_account_id)
            if account_sql:
                where.append(account_sql)
                params.extend(account_params)
            if groups_only:
                where.append("dialog_type IN ('group', 'channel')")
            elif dialog_type:
                where.append("dialog_type = ?")
                params.append(dialog_type)
            if query:
                where.append("(dialog_name LIKE ? COLLATE NOCASE OR dialog_id LIKE ?)")
                like = f"%{query}%"
                params.extend([like, like])
            account_select = "account_id" if has_account_id else "'default' AS account_id"
            group_by = "account_id, dialog_id" if has_account_id else "dialog_id"
            sql = f"""
                SELECT {account_select}, dialog_id, MAX(dialog_name) AS dialog_name, MAX(dialog_type) AS dialog_type,
                       COUNT(*) AS message_count, MAX(date) AS latest_date
                FROM messages
            """
            if where:
                sql += " WHERE " + " AND ".join(where)
            sql += f" GROUP BY {group_by} HAVING COUNT(*) >= ? ORDER BY latest_date DESC LIMIT ?"
            params.extend([max(0, int(min_count)), limit])
            rows = conn.execute(sql, params).fetchall()
        return [
            {
                "account_id": row["account_id"],
                "id": row["dialog_id"],
                "name": row["dialog_name"],
                "type": row["dialog_type"],
                "message_count": row["message_count"],
                "latest_date": row["latest_date"],
            }
            for row in rows
        ]

    def recent_messages(
        self,
        minutes: int | None = None,
        dialog_id: str | None = None,
        dialog_type: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        no_text: bool = False,
    ) -> list[dict[str, Any]]:
        where: list[str] = []
        params: list[Any] = []
        if minutes is not None:
            since = (datetime.now(timezone.utc) - timedelta(minutes=int(minutes))).isoformat()
            where.append("date >= ?")
            params.append(since)
        if dialog_id:
            where.append("dialog_id = ?")
            params.append(str(dialog_id))
        if dialog_type:
            where.append("dialog_type = ?")
            params.append(dialog_type)
        if sender:
            where.append("sender_name LIKE ? COLLATE NOCASE")
            params.append(f"%{sender}%")
        return self._query_messages(where, params, limit, no_text=no_text)

    def search_messages(
        self,
        query: str,
        dialog_id: str | None = None,
        dialog_type: str | None = None,
        since: str | None = None,
        until: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        no_text: bool = False,
    ) -> list[dict[str, Any]]:
        if not query:
            raise ValueError("search query is required")
        where = ["text LIKE ? COLLATE NOCASE"]
        params: list[Any] = [f"%{query}%"]
        if dialog_id:
            where.append("dialog_id = ?")
            params.append(str(dialog_id))
        if dialog_type:
            where.append("dialog_type = ?")
            params.append(dialog_type)
        if since:
            where.append("date >= ?")
            params.append(since)
        if until:
            where.append("date <= ?")
            params.append(until)
        if sender:
            where.append("sender_name LIKE ? COLLATE NOCASE")
            params.append(f"%{sender}%")
        return self._query_messages(where, params, limit, no_text=no_text)

    def get_thread(self, dialog_id: str, message_id: int, context: int = 10, max_depth: int = 20) -> dict[str, Any]:
        anchor = self.get_message(dialog_id, message_id)
        if anchor is None:
            raise DBUnavailable(f"message not found: dialog={dialog_id} msg_id={message_id}")

        parents: list[dict[str, Any]] = []
        seen = {anchor["msg_id"]}
        current = anchor
        for _ in range(max(0, int(max_depth))):
            parent_id = current.get("reply_to_id")
            if not parent_id or parent_id in seen:
                break
            parent = self.get_message(dialog_id, int(parent_id))
            if parent is None:
                break
            parents.insert(0, parent)
            seen.add(parent["msg_id"])
            current = parent

        with self.connect() as conn:
            has_account_id = self._has_account_id(conn)
            account_sql, account_params = self._account_where(has_account_id)
            prefix = f"{account_sql} AND " if account_sql else ""
            reply_rows = conn.execute(
                self._select_sql(f"{prefix}dialog_id = ? AND reply_to_id = ?", "date ASC", has_account_id),
                [*account_params, str(dialog_id), int(message_id), clamp_limit(context, default=10)],
            ).fetchall()
            before_rows = conn.execute(
                self._select_sql(f"{prefix}dialog_id = ? AND date < ?", "date DESC", has_account_id),
                [*account_params, str(dialog_id), anchor["date"], clamp_limit(context, default=10)],
            ).fetchall()
            after_rows = conn.execute(
                self._select_sql(f"{prefix}dialog_id = ? AND date > ?", "date ASC", has_account_id),
                [*account_params, str(dialog_id), anchor["date"], clamp_limit(context, default=10)],
            ).fetchall()
        before = [self._row_to_message(row) for row in reversed(before_rows)]
        after = [self._row_to_message(row) for row in after_rows]
        return {
            "account_id": anchor.get("account_id", "default"),
            "dialog_id": str(dialog_id),
            "message_id": int(message_id),
            "source": "sqlite-readonly",
            "anchor": anchor,
            "parents": parents,
            "replies": [self._row_to_message(row) for row in reply_rows],
            "context": before + after,
        }

    def get_message(self, dialog_id: str, message_id: int) -> dict[str, Any] | None:
        with self.connect() as conn:
            has_account_id = self._has_account_id(conn)
            account_sql, account_params = self._account_where(has_account_id)
            where = f"{account_sql} AND dialog_id = ? AND msg_id = ?" if account_sql else "dialog_id = ? AND msg_id = ?"
            row = conn.execute(
                self._select_sql(where, "date DESC", has_account_id),
                [*account_params, str(dialog_id), int(message_id), 1],
            ).fetchone()
        return self._row_to_message(row) if row else None

    def _query_messages(self, where: list[str], params: list[Any], limit: int, no_text: bool = False) -> list[dict[str, Any]]:
        with self.connect() as conn:
            has_account_id = self._has_account_id(conn)
            account_sql, account_params = self._account_where(has_account_id)
            final_where = list(where)
            final_params = list(params)
            if account_sql:
                final_where.insert(0, account_sql)
                final_params = [*account_params, *final_params]
            where_sql = " AND ".join(final_where) if final_where else "1=1"
            rows = conn.execute(self._select_sql(where_sql, "date DESC", has_account_id), [*final_params, clamp_limit(limit)]).fetchall()
        messages = [self._row_to_message(row) for row in rows]
        if no_text:
            for message in messages:
                message["text"] = None
        return messages

    def _account_where(self, has_account_id: bool) -> tuple[str, list[Any]]:
        if not self.account_id:
            return "", []
        if has_account_id:
            return "account_id = ?", [self.account_id]
        if self.account_id == "default":
            return "", []
        return "1=0", []

    @staticmethod
    def _select_sql(where_sql: str, order_by: str, has_account_id: bool = False) -> str:
        account_select = "account_id" if has_account_id else "'default' AS account_id"
        return f"""
            SELECT {account_select}, dialog_id, dialog_name, dialog_type, msg_id, sender_name, text, date, reply_to_id
            FROM messages WHERE {where_sql}
            ORDER BY {order_by} LIMIT ?
        """

    @staticmethod
    def _has_account_id(conn: sqlite3.Connection) -> bool:
        return any(row[1] == "account_id" for row in conn.execute("PRAGMA table_info(messages)").fetchall())

    @staticmethod
    def _row_to_message(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "account_id": row["account_id"],
            "dialog_id": row["dialog_id"],
            "dialog": row["dialog_name"],
            "type": row["dialog_type"],
            "msg_id": row["msg_id"],
            "sender": row["sender_name"],
            "text": row["text"],
            "date": row["date"],
            "reply_to_id": row["reply_to_id"],
        }

def thread_to_markdown(thread: dict[str, Any]) -> str:
    lines = [
        "# Telegram thread export",
        "",
        f"- Dialog ID: `{thread['dialog_id']}`",
        f"- Anchor message: `{thread['message_id']}`",
        f"- Source: `{thread.get('source', 'sqlite-readonly')}`",
        "",
    ]
    for label in ("parents", "anchor", "replies", "context"):
        items = [thread[label]] if label == "anchor" else thread.get(label, [])
        lines.extend([f"## {label.title()}", ""])
        if not items:
            lines.extend(["_None._", ""])
            continue
        for msg in items:
            text = (msg.get("text") or "").replace("\n", " ")
            lines.append(f"- `{msg.get('date')}` `{msg.get('msg_id')}` **{msg.get('sender') or 'unknown'}**: {text}")
        lines.append("")
    return "\n".join(lines)


def messages_to_jsonl(messages: list[dict[str, Any]]) -> str:
    return "\n".join(json.dumps(message, ensure_ascii=False) for message in messages) + ("\n" if messages else "")
