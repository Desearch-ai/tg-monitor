#!/usr/bin/env python3
"""Export recent Telegram context for hot-topic/reply suggestion cron.

Reads tg-monitor SQLite, defaults to the Nerds group, and emits JSON for an
LLM summarizer. No secrets, no Telegram API calls.
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path(__file__).with_name("monitor.db")
DEFAULT_DIALOG = "-1002564889965"  # ☝️🤓 τhe nerds 🧠🥼
STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "you", "are", "was", "have", "has", "but", "not",
    "just", "what", "when", "where", "will", "can", "cant", "dont", "they", "them", "about", "into", "your",
    "https", "http", "com", "lol", "yeah", "yes", "no", "gm", "im", "its", "i", "a", "to", "of", "in", "on",
    "is", "it", "as", "at", "be", "or", "if", "we", "our", "us", "do", "so", "my", "me", "he", "she", "there",
}


def parse_dt(value: str) -> datetime:
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def tokenize(text: str) -> list[str]:
    words = re.findall(r"[A-Za-z][A-Za-z0-9_]{2,}|[τ][A-Za-z0-9_]+", text.lower())
    return [w for w in words if w not in STOPWORDS and len(w) > 2]


def fetch_rows(conn: sqlite3.Connection, since: datetime, dialog: str | None, include_dms: bool, limit: int):
    query = """
        SELECT dialog_id, dialog_name, dialog_type, msg_id, sender_name, text, date, reply_to_id
        FROM messages
        WHERE date >= ?
    """
    params: list[object] = [since.isoformat()]
    if dialog:
        query += " AND dialog_id = ?"
        params.append(dialog)
    if not include_dms:
        query += " AND dialog_type != 'dm'"
    query += " ORDER BY date ASC LIMIT ?"
    params.append(limit)
    return conn.execute(query, params).fetchall()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--dialog", default=DEFAULT_DIALOG, help="Telegram dialog id, or 'all'")
    ap.add_argument("--hours", type=float, default=4.0)
    ap.add_argument("--reply-recency-minutes", type=int, default=90)
    ap.add_argument("--limit", type=int, default=350)
    ap.add_argument("--include-dms", action="store_true")
    ap.add_argument("--output", default="-")
    args = ap.parse_args()

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=args.hours)
    reply_since = now - timedelta(minutes=args.reply_recency_minutes)
    dialog_filter = None if args.dialog == "all" else args.dialog

    conn = sqlite3.connect(args.db)
    rows = fetch_rows(conn, since, dialog_filter, args.include_dms, args.limit)

    # Parent/reply lookup for context, including parents slightly older than the window.
    by_key = {(str(r[0]), int(r[3])): r for r in rows}
    parent_keys = [(str(r[0]), int(r[7])) for r in rows if r[7] is not None and (str(r[0]), int(r[7])) not in by_key]
    parents = {}
    for dialog_id, msg_id in parent_keys[:150]:
        p = conn.execute(
            "SELECT sender_name, text, date FROM messages WHERE dialog_id=? AND msg_id=?",
            (dialog_id, msg_id),
        ).fetchone()
        if p:
            parents[f"{dialog_id}:{msg_id}"] = {"sender": p[0], "text": p[1], "date": p[2]}
    conn.close()

    dialog_counts = Counter()
    sender_counts = Counter()
    keyword_counts = Counter()
    current_keyword_counts = Counter()
    reply_threads = defaultdict(list)
    messages = []

    for r in rows:
        dialog_id, dialog_name, dialog_type, msg_id, sender, text, date, reply_to_id = r
        dt = parse_dt(date)
        age_min = round((now - dt).total_seconds() / 60, 1)
        dialog_counts[(dialog_id, dialog_name, dialog_type)] += 1
        sender_counts[sender or "Unknown"] += 1
        toks = tokenize(text or "")
        keyword_counts.update(toks)
        if dt >= reply_since:
            current_keyword_counts.update(toks)
        if reply_to_id is not None:
            reply_threads[f"{dialog_id}:{reply_to_id}"].append(int(msg_id))

        item = {
            "dialog_id": str(dialog_id),
            "dialog": dialog_name,
            "type": dialog_type,
            "msg_id": int(msg_id),
            "sender": sender,
            "date": date,
            "age_minutes": age_min,
            "reply_to_id": int(reply_to_id) if reply_to_id is not None else None,
            "text": (text or "").strip(),
        }
        if reply_to_id is not None:
            parent = parents.get(f"{dialog_id}:{reply_to_id}") or by_key.get((str(dialog_id), int(reply_to_id)))
            if parent:
                if isinstance(parent, tuple):
                    item["reply_parent"] = {"sender": parent[4], "text": parent[5], "date": parent[6]}
                else:
                    item["reply_parent"] = parent
        messages.append(item)

    # Candidate reply anchors must be recent, substantive, and not tiny acknowledgements.
    candidates = []
    for m in messages:
        if parse_dt(m["date"]) < reply_since:
            continue
        text = m["text"]
        if len(text) < 25:
            continue
        if re.fullmatch(r"(?i)(gm|lol|yes|no|yeah|ok|thanks|same)[.! ]*", text.strip()):
            continue
        candidates.append(m)
    candidates = candidates[-40:]

    payload = {
        "generated_at": now.isoformat(),
        "window": {
            "hours": args.hours,
            "since": since.isoformat(),
            "reply_suggestions_should_anchor_after": reply_since.isoformat(),
            "rule": "Analyze the whole window for topics, but suggested replies should target active/recent messages only.",
        },
        "source": {
            "db": str(Path(args.db).resolve()),
            "dialog_filter": dialog_filter or "all_non_dm_dialogs" if not args.include_dms else dialog_filter or "all_dialogs_including_dms",
            "include_dms": bool(args.include_dms),
        },
        "stats": {
            "message_count": len(messages),
            "dialogs": [
                {"dialog_id": k[0], "name": k[1], "type": k[2], "messages": v}
                for k, v in dialog_counts.most_common(20)
            ],
            "top_senders": sender_counts.most_common(20),
            "top_keywords_4h": keyword_counts.most_common(30),
            "top_keywords_recent": current_keyword_counts.most_common(30),
            "reply_threads": [
                {"thread_root": k, "reply_count_in_window": len(v), "recent_reply_ids": v[-8:]}
                for k, v in sorted(reply_threads.items(), key=lambda kv: len(kv[1]), reverse=True)[:20]
            ],
        },
        "messages": messages,
        "reply_anchor_candidates": candidates,
    }

    out = json.dumps(payload, ensure_ascii=False, indent=2)
    if args.output == "-":
        print(out)
    else:
        Path(args.output).write_text(out, encoding="utf-8")


if __name__ == "__main__":
    main()
