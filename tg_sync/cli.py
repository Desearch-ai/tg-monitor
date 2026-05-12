"""Read-only tgsync CLI for Telegram Monitor."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .accounts import AccountRegistry
from .api_client import ApiClient, ApiUnavailable
from .backfill import build_backfill_request, run_backfill
from .config import DEFAULT_API_URL, clamp_limit, resolve_db_path
from .store import DBUnavailable, ReadOnlyStore, messages_to_jsonl, thread_to_markdown

EXIT_OK = 0
EXIT_RUNTIME_ERROR = 1
EXIT_BAD_ARGS = 2
EXIT_API_UNAVAILABLE = 3
EXIT_DB_UNAVAILABLE = 4


def _common_parser(defaults: bool) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--api-url", default=DEFAULT_API_URL if defaults else argparse.SUPPRESS, help="Local Telegram Monitor API URL")
    common.add_argument("--db", default=None if defaults else argparse.SUPPRESS, help="Path to monitor.db (defaults TG_MONITOR_DB, DB_PATH, monitor.db)")
    common.add_argument("--json", action="store_true", default=False if defaults else argparse.SUPPRESS, help="Emit machine-readable JSON only")
    common.add_argument("--limit", type=int, default=None if defaults else argparse.SUPPRESS, help="Maximum rows to return (hard-capped)")
    common.add_argument("--no-text", "--redact-text", dest="no_text", action="store_true", default=False if defaults else argparse.SUPPRESS, help="Redact message bodies")
    common.add_argument("--account", default=None if defaults else argparse.SUPPRESS, help="Named sync account (defaults active account)")
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_parser(defaults=True)
    sub_common = _common_parser(defaults=False)

    parser = argparse.ArgumentParser(prog="tgsync", parents=[common], description="Read-only Telegram Monitor sync CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", parents=[sub_common], help="Show service status")
    sub.add_parser("health", parents=[sub_common], help="Show service health")


    accounts = sub.add_parser("accounts", parents=[sub_common], help="Manage local sync account registry")
    account_sub = accounts.add_subparsers(dest="account_command", required=True)
    account_sub.add_parser("list", parents=[sub_common], help="List configured accounts")
    account_sub.add_parser("status", parents=[sub_common], help="Show active/configured account")
    add = account_sub.add_parser("add", parents=[sub_common], help="Add or update an account entry (no secrets stored)")
    add.add_argument("name")
    add.add_argument("--label")
    add.add_argument("--session", dest="session_path")
    add.add_argument("--credentials-source")
    switch = account_sub.add_parser("switch", parents=[sub_common], help="Switch active account")
    switch.add_argument("name")

    sync = sub.add_parser("sync", parents=[sub_common], help="Operator-initiated bounded sync actions")
    sync_sub = sync.add_subparsers(dest="sync_command", required=True)
    backfill = sync_sub.add_parser("backfill", parents=[sub_common], help="Backfill older Telegram messages into the selected account DB")
    backfill.add_argument("--dialog", required=True, help="Dialog id/username to read")
    backfill.add_argument("--before-id", type=int)
    backfill.add_argument("--before-date", help="ISO timestamp; passed as an older-than cursor")
    backfill.add_argument("--dry-run", action="store_true", help="Validate and print the bounded read plan without Telegram/network access")

    for name in ("chats", "dialogs", "groups"):
        p = sub.add_parser(name, parents=[sub_common], help="List known dialogs/chats")
        p.add_argument("--type", choices=["group", "channel", "dm"], dest="dialog_type")
        p.add_argument("--query", help="Filter by dialog name/id")
        p.add_argument("--min-count", type=int, default=0)

    for name in ("messages", "recent"):
        p = sub.add_parser(name, parents=[sub_common], help="Show recent messages")
        p.add_argument("--minutes", type=int, default=60)
        p.add_argument("--dialog")
        p.add_argument("--type", choices=["group", "channel", "dm"], dest="dialog_type")
        p.add_argument("--sender")

    p = sub.add_parser("search", parents=[sub_common], help="Search local stored messages")
    p.add_argument("query")
    p.add_argument("--dialog")
    p.add_argument("--type", choices=["group", "channel", "dm"], dest="dialog_type")
    p.add_argument("--since")
    p.add_argument("--until")
    p.add_argument("--sender")
    p.add_argument("--export", dest="export_path")

    p = sub.add_parser("thread", parents=[sub_common], help="Show a local thread around a message")
    p.add_argument("--dialog", required=True)
    p.add_argument("--message-id", type=int, required=True)
    p.add_argument("--context", type=int, default=10)
    p.add_argument("--max-depth", type=int, default=20)

    p = sub.add_parser("export", parents=[sub_common], help="Export thread or messages from local DB")
    export_sub = p.add_subparsers(dest="export_command", required=True)
    tp = export_sub.add_parser("thread", parents=[sub_common], help="Export a thread")
    tp.add_argument("--dialog", required=True)
    tp.add_argument("--message-id", type=int, required=True)
    tp.add_argument("--context", type=int, default=10)
    tp.add_argument("--max-depth", type=int, default=20)
    tp.add_argument("--format", choices=["json", "markdown"], default="json")
    tp.add_argument("--output", required=True)
    mp = export_sub.add_parser("messages", parents=[sub_common], help="Export messages")
    mp.add_argument("--dialog")
    mp.add_argument("--type", choices=["group", "channel", "dm"], dest="dialog_type")
    mp.add_argument("--since")
    mp.add_argument("--until")
    mp.add_argument("--format", choices=["json", "jsonl", "markdown"], default="jsonl")
    mp.add_argument("--output", required=True)

    p = sub.add_parser("tail", parents=[sub_common], help="Poll recent local messages until interrupted")
    p.add_argument("--dialog")
    p.add_argument("--type", choices=["group", "channel", "dm"], dest="dialog_type")
    p.add_argument("--contains")
    p.add_argument("--interval", type=float, default=5.0)

    wp = sub.add_parser("watch", parents=[sub_common], help="Poll status until interrupted")
    watch_sub = wp.add_subparsers(dest="watch_command", required=True)
    sp = watch_sub.add_parser("status", parents=[sub_common], help="Watch status changes")
    sp.add_argument("--interval", type=float, default=5.0)
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        if not (args.command == "sync" and getattr(args, "sync_command", None) == "backfill"):
            args.limit = clamp_limit(args.limit, default=50)
    except (SystemExit, ValueError) as exc:
        return EXIT_BAD_ARGS if not isinstance(exc, SystemExit) else int(exc.code or 0)

    try:
        if args.command in {"status", "health"}:
            return _cmd_status(args, health=args.command == "health")
        if args.command == "accounts":
            return _cmd_accounts(args)
        if args.command == "sync" and args.sync_command == "backfill":
            return _cmd_sync_backfill(args)
        if args.command in {"chats", "dialogs", "groups"}:
            return _cmd_dialogs(args, groups_only=args.command == "groups")
        if args.command in {"messages", "recent"}:
            return _cmd_messages(args)
        if args.command == "search":
            return _cmd_search(args)
        if args.command == "thread":
            return _cmd_thread(args)
        if args.command == "export":
            return _cmd_export(args)
        if args.command == "tail":
            return _cmd_tail(args)
        if args.command == "watch" and args.watch_command == "status":
            return _cmd_watch_status(args)
    except DBUnavailable as exc:
        _emit(args, {"ok": False, "error": str(exc), "db_path": str(resolve_db_path(args.db))}, error=True)
        return EXIT_DB_UNAVAILABLE
    except ApiUnavailable as exc:
        _emit(args, {"ok": False, "api_url": args.api_url, "error": str(exc)}, error=True)
        return EXIT_API_UNAVAILABLE
    except ValueError as exc:
        _emit(args, {"ok": False, "error": str(exc)}, error=True)
        return EXIT_BAD_ARGS
    except Exception as exc:  # keep CLI errors safe and non-secret
        _emit(args, {"ok": False, "error": str(exc)}, error=True)
        return EXIT_RUNTIME_ERROR
    return EXIT_BAD_ARGS


def _cmd_status(args: argparse.Namespace, health: bool = False) -> int:
    client = ApiClient(args.api_url)
    payload = client.health() if health else client.status()
    data = {
        "ok": True,
        "api_url": args.api_url,
        "checked_at": datetime.now(timezone.utc).isoformat(),
        **payload,
    }
    _emit(args, data, table=_format_status(data))
    return EXIT_OK


def _cmd_accounts(args: argparse.Namespace) -> int:
    registry = AccountRegistry()
    if args.account_command == "list":
        accounts = [account.to_json() for account in registry.list_accounts()]
        payload = {"ok": True, "active_account": registry.active_account(getattr(args, "account", None)).id, "accounts": accounts, "config_path": str(registry.path)}
        _emit(args, payload, table=_format_accounts(accounts))
        return EXIT_OK
    if args.account_command == "status":
        account = registry.active_account(getattr(args, "account", None))
        payload = {"ok": True, "active_account": account.id, "account": account.to_json(), "config_path": str(registry.path)}
        _emit(args, payload, table=_format_account_status(payload))
        return EXIT_OK
    if args.account_command == "add":
        account = registry.add_account(args.name, label=args.label, session_path=args.session_path, db_path=args.db, credentials_source=args.credentials_source)
        payload = {"ok": True, "active_account": registry.active_account().id, "account": account.to_json(), "config_path": str(registry.path)}
        _emit(args, payload, table=_format_account_status(payload))
        return EXIT_OK
    if args.account_command == "switch":
        account = registry.switch(args.name)
        payload = {"ok": True, "active_account": account.id, "account": account.to_json(), "config_path": str(registry.path)}
        _emit(args, payload, table=_format_account_status(payload))
        return EXIT_OK
    return EXIT_BAD_ARGS


def _cmd_sync_backfill(args: argparse.Namespace) -> int:
    account = AccountRegistry().active_account(getattr(args, "account", None))
    if args.db:
        account = type(account)(**{**account.__dict__, "db_path": resolve_db_path(args.db)})
    request = build_backfill_request(args.dialog, args.limit, before_id=args.before_id, before_date=args.before_date, dry_run=args.dry_run)
    payload = run_backfill(account, request)
    _emit(args, payload, table=_format_backfill(payload))
    return EXIT_OK


def _cmd_dialogs(args: argparse.Namespace, groups_only: bool = False) -> int:
    dialogs: list[dict[str, Any]] = []
    source = "api"
    api_error: ApiUnavailable | None = None
    try:
        dialogs = ApiClient(args.api_url).groups() if groups_only else ApiClient(args.api_url).dialogs()
        if groups_only:
            dialogs = [{**d, "type": d.get("type") or "group/channel"} for d in dialogs]
    except ApiUnavailable as exc:
        api_error = exc
    try:
        db_dialogs = _store(args).list_dialogs(
            dialog_type=args.dialog_type,
            limit=args.limit,
            query=args.query,
            min_count=args.min_count,
            groups_only=groups_only,
        )
        dialogs = db_dialogs
        source = "sqlite-readonly"
    except DBUnavailable:
        if not dialogs and api_error is not None:
            raise api_error
        dialogs = dialogs[: args.limit]
        if args.dialog_type:
            dialogs = [d for d in dialogs if d.get("type") == args.dialog_type]
    payload = {"ok": True, "source": source, "count": len(dialogs), "dialogs": dialogs}
    _emit(args, payload, table=_format_dialogs(dialogs))
    return EXIT_OK


def _cmd_messages(args: argparse.Namespace) -> int:
    db_requested = bool(args.db or getattr(args, "account", None) or os.environ.get("TG_MONITOR_DB") or os.environ.get("DB_PATH"))
    if db_requested:
        messages = _store(args).recent_messages(
            minutes=args.minutes,
            dialog_id=args.dialog,
            dialog_type=args.dialog_type,
            sender=args.sender,
            limit=args.limit,
            no_text=args.no_text,
        )
        data = {"ok": True, "source": "sqlite-readonly", "sort": "date_desc", "count": len(messages), "minutes": args.minutes, "messages": messages}
    else:
        payload = ApiClient(args.api_url).messages(args.minutes, args.dialog, args.dialog_type, args.limit)
        messages = list(reversed(payload.get("messages", [])))
        if args.no_text:
            for msg in messages:
                msg["text"] = None
        data = {"ok": True, "source": "api", "sort": "date_desc", **payload, "messages": messages}
    _emit(args, data, table=_format_messages(data["messages"]))
    return EXIT_OK


def _cmd_search(args: argparse.Namespace) -> int:
    messages = _store(args).search_messages(
        args.query,
        dialog_id=args.dialog,
        dialog_type=args.dialog_type,
        since=args.since,
        until=args.until,
        sender=args.sender,
        limit=args.limit,
        no_text=args.no_text,
    )
    payload = {"ok": True, "source": "sqlite-readonly", "query": args.query, "count": len(messages), "messages": messages}
    if args.export_path:
        _write_text(Path(args.export_path), messages_to_jsonl(messages))
        payload["export_path"] = args.export_path
    _emit(args, payload, table=_format_messages(messages))
    return EXIT_OK


def _cmd_thread(args: argparse.Namespace) -> int:
    thread = _store(args).get_thread(args.dialog, args.message_id, context=args.context, max_depth=args.max_depth)
    if args.no_text:
        _redact_thread(thread)
    _emit(args, {"ok": True, **thread}, table=thread_to_markdown(thread))
    return EXIT_OK


def _cmd_export(args: argparse.Namespace) -> int:
    store = _store(args)
    if args.export_command == "thread":
        thread = store.get_thread(args.dialog, args.message_id, context=args.context, max_depth=args.max_depth)
        if args.no_text:
            _redact_thread(thread)
        body = thread_to_markdown(thread) if args.format == "markdown" else json.dumps(thread, ensure_ascii=False, indent=2) + "\n"
        _write_text(Path(args.output), body)
        _emit(args, {"ok": True, "export_path": args.output, "format": args.format})
        return EXIT_OK
    messages = store.search_messages("%", dialog_id=args.dialog, dialog_type=args.dialog_type, since=args.since, until=args.until, limit=args.limit, no_text=args.no_text)
    if args.format == "jsonl":
        body = messages_to_jsonl(messages)
    elif args.format == "json":
        body = json.dumps({"messages": messages}, ensure_ascii=False, indent=2) + "\n"
    else:
        body = "# Telegram messages export\n\n" + "\n".join(
            f"- `{m['date']}` `{m['dialog_id']}/{m['msg_id']}` **{m.get('sender') or 'unknown'}**: {(m.get('text') or '').replace(chr(10), ' ')}"
            for m in messages
        ) + "\n"
    _write_text(Path(args.output), body)
    _emit(args, {"ok": True, "export_path": args.output, "format": args.format, "count": len(messages)})
    return EXIT_OK


def _cmd_tail(args: argparse.Namespace) -> int:
    seen: set[tuple[str, int]] = set()
    try:
        while True:
            messages = _store(args).recent_messages(dialog_id=args.dialog, dialog_type=args.dialog_type, limit=args.limit)
            if args.contains:
                messages = [m for m in messages if args.contains.lower() in (m.get("text") or "").lower()]
            fresh = [m for m in reversed(messages) if (m["dialog_id"], m["msg_id"]) not in seen]
            for msg in fresh:
                seen.add((msg["dialog_id"], msg["msg_id"]))
                _emit(args, {"ok": True, "message": msg}, table=_format_messages([msg]))
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        return EXIT_OK


def _cmd_watch_status(args: argparse.Namespace) -> int:
    previous = None
    try:
        while True:
            payload = ApiClient(args.api_url).status()
            snapshot = json.dumps(payload, sort_keys=True)
            if snapshot != previous:
                _emit(args, {"ok": True, "api_url": args.api_url, **payload}, table=_format_status({"api_url": args.api_url, **payload}))
                previous = snapshot
            time.sleep(max(1.0, args.interval))
    except KeyboardInterrupt:
        return EXIT_OK


def _redact_thread(thread: dict[str, Any]) -> None:
    buckets = [thread.get("parents", []), thread.get("replies", []), thread.get("context", [])]
    if thread.get("anchor"):
        buckets.append([thread["anchor"]])
    for bucket in buckets:
        for message in bucket:
            message["text"] = None


def _emit(args: argparse.Namespace, payload: dict[str, Any], table: str | None = None, error: bool = False) -> None:
    if getattr(args, "json", False):
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(table if table is not None else _format_status(payload))


def _store(args: argparse.Namespace) -> ReadOnlyStore:
    account = AccountRegistry().active_account(getattr(args, "account", None))
    db_path = args.db or str(account.db_path)
    return ReadOnlyStore(db_path, account_id=account.id)


def _format_status(data: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"API: {data.get('api_url', DEFAULT_API_URL)}",
            f"Status: {data.get('status', 'unknown')}",
            f"Telegram ready: {data.get('telegram_ready', 'unknown')}",
            f"Total messages: {data.get('total_messages', 'unknown')}",
            f"By type: {data.get('by_type', {})}",
        ] + ([f"Error: {data['error']}"] if data.get("error") else [])
    )


def _format_accounts(accounts: list[dict[str, Any]]) -> str:
    lines = ["active\taccount_id\tsession_path\tdb_path\tcredentials_source\tlabel"]
    for account in accounts:
        lines.append(f"{ '*' if account.get('active') else ''}\t{account.get('id')}\t{account.get('session_path')}\t{account.get('db_path')}\t{account.get('credentials_source')}\t{account.get('label')}")
    return "\n".join(lines)


def _format_account_status(payload: dict[str, Any]) -> str:
    account = payload.get("account", {})
    return "\n".join([
        f"Active account: {payload.get('active_account')}",
        f"Session path: {account.get('session_path')}",
        f"DB path: {account.get('db_path')}",
        f"Credentials: {account.get('credentials_source')}",
        f"Config: {payload.get('config_path')}",
    ])


def _format_backfill(payload: dict[str, Any]) -> str:
    request = payload.get("request", {})
    account = payload.get("account", {})
    if payload.get("dry_run"):
        return f"DRY RUN: would read up to {request.get('limit')} older messages from {request.get('dialog')} for account {account.get('id')} into {account.get('db_path')} (Telegram writes forbidden)"
    return f"Backfill complete: fetched={payload.get('fetched')} saved={payload.get('saved')} account={account.get('id')} dialog={request.get('dialog')}"


def _format_dialogs(dialogs: list[dict[str, Any]]) -> str:
    lines = ["dialog_id\ttype\tmessage_count\tlatest_date\tname"]
    for d in dialogs:
        lines.append(f"{d.get('id')}\t{d.get('type')}\t{d.get('message_count', '')}\t{d.get('latest_date', '')}\t{d.get('name')}")
    return "\n".join(lines)


def _format_messages(messages: list[dict[str, Any]]) -> str:
    lines = ["date\tdialog\ttype\tmsg_id\tsender\treply_to_id\ttext_preview"]
    for msg in messages:
        text = (msg.get("text") or "").replace("\n", " ")[:120]
        lines.append(f"{msg.get('date')}\t{msg.get('dialog')}\t{msg.get('type')}\t{msg.get('msg_id')}\t{msg.get('sender')}\t{msg.get('reply_to_id')}\t{text}")
    return "\n".join(lines)


def _write_text(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
