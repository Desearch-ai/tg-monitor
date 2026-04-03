"""
Telegram Monitor
================
ყველა ჯგუფი და პირადი მიმოწერა — 24სთ ისტორია პირველ გაშვებაზე,
შემდეგ unseen-only. HTTP API on localhost:8765.
"""

import asyncio
import json
import os
import signal
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import (
    Channel, Chat, User,
    PeerChannel, PeerChat, PeerUser,
    MessageReplyHeader,
)
from aiohttp import web

load_dotenv()

sys.stdout = open(sys.stdout.fileno(), mode='w', buffering=1)

# ── Config ────────────────────────────────────────────────────────────────────
API_ID    = int(os.environ["TG_API_ID"])
API_HASH  = os.environ["TG_API_HASH"]
PHONE     = os.environ["TG_PHONE"]
MY_USER_ID = int(os.environ.get("TG_MY_USER_ID", "0"))
API_PORT  = int(os.environ.get("API_PORT", "8765"))

# ── Shutdown event ────────────────────────────────────────────────────────────
# Set by SIGTERM / SIGINT signal handlers installed in main().
# Used by monitor_loop() to exit cleanly instead of being force-killed by asyncio.
_shutdown_event: asyncio.Event | None = None

# ── Startup state ─────────────────────────────────────────────────────────────
# False until Telegram has connected AND dialogs are loaded.
# /status returns {"status":"starting"} until then so the watchdog sees HTTP 200
# immediately on startup instead of connection-refused → SIGINT loop.
_telegram_ready: bool = False

# ── DB ────────────────────────────────────────────────────────────────────────
DB_PATH = "monitor.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            dialog_id     TEXT,
            dialog_name   TEXT,
            dialog_type   TEXT,
            msg_id        INTEGER,
            sender_id     INTEGER,
            sender_name   TEXT,
            text          TEXT,
            date          TEXT,
            reply_to_id   INTEGER,
            UNIQUE(dialog_id, msg_id)
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS last_seen (
            dialog_id  TEXT PRIMARY KEY,
            message_id INTEGER DEFAULT 0
        )
    """)
    conn.commit()
    conn.close()

def get_last_seen(dialog_id: str) -> int:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT message_id FROM last_seen WHERE dialog_id=?", (dialog_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else 0

def set_last_seen(dialog_id: str, message_id: int):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "INSERT OR REPLACE INTO last_seen (dialog_id, message_id) VALUES (?,?)",
        (dialog_id, message_id),
    )
    conn.commit()
    conn.close()

def save_messages(dialog_id: str, dialog_name: str, dialog_type: str, msgs: list[dict]):
    conn = sqlite3.connect(DB_PATH)
    for m in msgs:
        try:
            conn.execute("""
                INSERT OR IGNORE INTO messages
                    (dialog_id, dialog_name, dialog_type,
                     msg_id, sender_id, sender_name, text, date, reply_to_id)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                dialog_id, dialog_name, dialog_type,
                m["id"], m["sender_id"], m["sender"],
                m["text"], m["date"], m.get("reply_to_id"),
            ))
        except Exception:
            pass
    conn.commit()
    conn.close()

def fetch_messages_db(minutes: int = 60, dialog_id: str = None,
                      dialog_type: str = None, limit: int = 200) -> list[dict]:
    since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
    conn  = sqlite3.connect(DB_PATH)
    query = """SELECT dialog_id, dialog_name, dialog_type,
                      msg_id, sender_name, text, date, reply_to_id
               FROM messages WHERE date >= ?"""
    params = [since]
    if dialog_id:
        query += " AND dialog_id = ?"
        params.append(dialog_id)
    if dialog_type:
        query += " AND dialog_type = ?"
        params.append(dialog_type)
    query += " ORDER BY date ASC LIMIT ?"
    params.append(limit)
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [
        {
            "dialog_id":   r[0],
            "dialog":      r[1],
            "type":        r[2],
            "msg_id":      r[3],
            "sender":      r[4],
            "text":        r[5],
            "date":        r[6],
            "reply_to_id": r[7],
        }
        for r in rows
    ]

# ── Telethon ──────────────────────────────────────────────────────────────────
client = TelegramClient("user_session", API_ID, API_HASH)

def _dialog_type(entity) -> str:
    if isinstance(entity, Channel):
        return "channel" if entity.broadcast else "group"
    if isinstance(entity, Chat):
        return "group"
    if isinstance(entity, User):
        return "dm"
    return "unknown"

async def fetch_dialog_messages(entity, dialog_id: str, dialog_name: str,
                                 dialog_type: str) -> list[dict]:
    """
    - last_id == 0  →  initial: last 24 hours
    - last_id  > 0  →  unseen only (msg.id > last_id)

    NOTE: We use client.get_messages() (returns a plain list) instead of
    client.iter_messages() (async iterator) for the same reason we use
    get_dialogs() in _scan_once(): Telethon's _MessagesIter.aclose() raises
    "'_MessagesIter' object has no attribute 'close'" when cancelled, which
    produces a secondary exception that crashes the process.  Using
    get_messages() avoids async-iterator cleanup entirely.
    """
    last_id    = get_last_seen(dialog_id)
    is_initial = (last_id == 0)
    since      = (datetime.now(timezone.utc) - timedelta(hours=24)) if is_initial else None

    msgs     = []
    new_last = last_id

    try:
        # Fetch messages as a plain list — safe to cancel (no async iterator)
        if is_initial:
            # For first load grab up to 500 newest messages; date filter below
            raw = await client.get_messages(entity, limit=500)
        else:
            # Only fetch messages newer than the last seen ID
            raw = await client.get_messages(entity, limit=200, min_id=last_id)

        for msg in raw:  # plain list, no async iterator, no aclose() issues
            # iter_messages returns newest-first; apply the same early-exit logic
            if not is_initial and msg.id <= last_id:
                break
            if since and (not msg.date or
                          msg.date.replace(tzinfo=timezone.utc) < since):
                break
            if not msg.text:
                continue

            sender_id   = 0
            sender_name = "Unknown"
            try:
                s = await msg.get_sender()
                if s:
                    sender_id   = getattr(s, "id", 0)
                    sender_name = (
                        getattr(s, "first_name", None)
                        or getattr(s, "title", None)
                        or "Unknown"
                    )
            except Exception:
                pass

            reply_to_id = None
            if isinstance(msg.reply_to, MessageReplyHeader):
                reply_to_id = msg.reply_to.reply_to_msg_id

            msgs.append({
                "id":          msg.id,
                "sender_id":   sender_id,
                "sender":      sender_name,
                "text":        msg.text,
                "date":        msg.date.isoformat(),
                "reply_to_id": reply_to_id,
            })
            if msg.id > new_last:
                new_last = msg.id
    except Exception as e:
        print(f"    ⚠️  {dialog_name}: {e}")
        return []

    msgs.reverse()

    if new_last > last_id:
        set_last_seen(dialog_id, new_last)
        save_messages(dialog_id, dialog_name, dialog_type, msgs)

    return msgs

SNAPSHOT_DIALOG  = os.environ.get("SNAPSHOT_DIALOG", "-1002564889965")
SNAPSHOT_MINUTES = int(os.environ.get("SNAPSHOT_MINUTES", "260"))
SNAPSHOT_LIMIT   = int(os.environ.get("SNAPSHOT_LIMIT", "200"))
SNAPSHOT_MAX_MIN = int(os.environ.get("SNAPSHOT_MAX_MINUTES", "2880"))  # 48h hard cap
SNAPSHOT_PATH    = os.path.join(os.path.dirname(DB_PATH), "snapshot_nerds.json")
SNAPSHOT_STATE   = os.path.join(os.path.dirname(DB_PATH), "snapshot_state.json")

def _get_last_snapshot_time():
    """Returns last successful snapshot write time as datetime (UTC), or None."""
    try:
        with open(SNAPSHOT_STATE, "r") as f:
            state = json.load(f)
        ts = state.get("last_written_at")
        if ts:
            return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)
    except Exception:
        pass
    return None

def _save_snapshot_time():
    try:
        with open(SNAPSHOT_STATE, "w") as f:
            json.dump({"last_written_at": datetime.now(timezone.utc).isoformat()}, f)
    except Exception:
        pass

def write_snapshot():
    """Write recent nerds messages to snapshot JSON.
    Window = max(SNAPSHOT_MINUTES, minutes since last successful write), capped at SNAPSHOT_MAX_MIN.
    This ensures catch-up after monitor downtime.
    """
    try:
        now_utc = datetime.now(timezone.utc)
        last_ts = _get_last_snapshot_time()

        if last_ts:
            elapsed = int((now_utc - last_ts).total_seconds() / 60)
            # Add 10min buffer so we don't miss messages at boundary
            window = min(max(elapsed + 10, SNAPSHOT_MINUTES), SNAPSHOT_MAX_MIN)
        else:
            window = SNAPSHOT_MINUTES

        is_catchup = window > SNAPSHOT_MINUTES + 30

        cutoff = (now_utc - timedelta(minutes=window)).isoformat()
        conn = sqlite3.connect(DB_PATH)
        rows = conn.execute(
            "SELECT msg_id, dialog_id, dialog_name, dialog_type, sender_name, text, date, reply_to_id "
            "FROM messages WHERE dialog_id=? AND date>=? ORDER BY date ASC LIMIT ?",
            (SNAPSHOT_DIALOG, cutoff, SNAPSHOT_LIMIT)
        ).fetchall()
        conn.close()

        msgs = [{"msg_id": r[0], "dialog_id": r[1], "dialog_name": r[2], "dialog_type": r[3],
                 "sender": r[4], "text": r[5], "date": r[6], "reply_to_id": r[7]} for r in rows]
        data = {
            "dialog_id": SNAPSHOT_DIALOG,
            "minutes": window,
            "count": len(msgs),
            "updated_at": now_utc.isoformat(),
            "is_catchup": is_catchup,
            "messages": msgs,
        }
        with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        catchup_note = f" [catch-up: {window//60}h{window%60}m]" if is_catchup else ""
        print(f"  📄 snapshot written: {len(msgs)} msgs → snapshot_nerds.json{catchup_note}")
        _save_snapshot_time()
    except Exception as e:
        print(f"  ⚠️ snapshot write error: {e}")

SCAN_TIMEOUT = 8 * 60  # 8 minutes max per full scan
SCAN_INTERVAL = 5 * 60  # 5 minutes between scans

async def _scan_once():
    """Single scan pass — fetches new messages from all dialogs.

    NOTE: We use client.get_dialogs() (returns a list) instead of
    client.iter_dialogs() (async iterator) to avoid a Python 3.11 asyncio
    cleanup bug.  When asyncio.wait_for() cancels a task that is mid-way
    through an async-for loop, it calls aclose() on the iterator.
    Telethon's _DialogsIter.aclose() can raise
    "'_DialogsIter' object has no attribute 'close'"
    which becomes a secondary exception ("During handling of the above
    exception, another exception occurred") that crashes the process.
    Using get_dialogs() fetches the dialog list upfront as a plain list,
    so there is no async-iterator to clean up when cancellation arrives.
    """
    total_new = 0
    # Fetch dialog list upfront — safe to cancel here because no iterator is open
    try:
        dialogs = await asyncio.wait_for(client.get_dialogs(limit=None), timeout=60)
    except asyncio.CancelledError:
        raise
    except asyncio.TimeoutError:
        print("    ⏱️ get_dialogs timed out — skipping scan")
        return 0
    except Exception as e:
        print(f"    ⚠️ get_dialogs failed: {e}")
        return 0

    for dialog in dialogs:
        entity      = dialog.entity
        dialog_type = _dialog_type(entity)
        dialog_name = dialog.name or str(dialog.id)
        dialog_id   = str(dialog.id)
        try:
            msgs = await asyncio.wait_for(
                fetch_dialog_messages(entity, dialog_id, dialog_name, dialog_type),
                timeout=30  # 30s per dialog max
            )
        except asyncio.TimeoutError:
            print(f"    ⏱️ timeout: {dialog_name} — skipping")
            continue
        except asyncio.CancelledError:
            raise  # propagate intentional cancellation
        except Exception as e:
            print(f"    ⚠️ {dialog_name}: {e}")
            continue
        if msgs:
            total_new += len(msgs)
            label = "init" if get_last_seen(dialog_id) == msgs[-1]["id"] else "new"
            print(f"    💾 [{dialog_type}] {dialog_name}: +{len(msgs)} msgs ({label})")
    return total_new

async def _interruptible_sleep(seconds: float) -> bool:
    """Sleep for `seconds`, but wake early if _shutdown_event is set.

    Returns True  if shutdown was requested (caller should exit).
    Returns False if sleep completed normally (caller should continue).
    """
    if _shutdown_event is None:
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            print("  ⚠️ sleep cancelled (spurious) — continuing immediately")
        return False

    try:
        await asyncio.wait_for(
            asyncio.shield(_shutdown_event.wait()),
            timeout=seconds,
        )
        # wait_for returned without TimeoutError → shutdown event was set
        return True
    except asyncio.TimeoutError:
        # Normal case: sleep elapsed without shutdown signal
        return False
    except asyncio.CancelledError:
        # Truly spurious cancellation during sleep
        if _shutdown_event.is_set():
            return True
        print("  ⚠️ sleep cancelled (spurious) — continuing immediately")
        return False


async def monitor_loop():
    """Main monitoring loop.

    Design notes on CancelledError handling
    ─────────────────────────────────────────
    CancelledError can arrive spuriously from several sources in Python 3.11:
      • Telethon's internal reconnect machinery
      • Event-loop cleanup on macOS (known asyncio quirk)

    We ABSORB spurious CancelledError rather than re-raise it.
    Real shutdown is signalled via _shutdown_event (set by SIGTERM/SIGINT
    signal handlers installed in main()).  This lets pm2 stop/restart the
    process cleanly (exit 0) rather than force-killing it (which was causing
    the DOWN alerts).
    """
    print("🚀 მონიტორინგი დაიწყო — ყოველ 5 წუთში (timeout: 8წთ/scan)")
    consecutive_errors = 0
    while not (_shutdown_event and _shutdown_event.is_set()):
        print(f"  🔄 [{datetime.now().strftime('%H:%M')}] scanning all dialogs…")
        total_new = 0
        try:
            total_new = await asyncio.wait_for(_scan_once(), timeout=SCAN_TIMEOUT)
            consecutive_errors = 0
        except asyncio.TimeoutError:
            print(f"  ⏱️ scan timed out after {SCAN_TIMEOUT//60}min — will retry")
            consecutive_errors += 1
        except asyncio.CancelledError:
            if _shutdown_event and _shutdown_event.is_set():
                print("  🛑 scan interrupted by shutdown signal — exiting")
                break
            # Absorb spurious cancellation — treat as transient error, keep running
            print("  ⚠️ scan cancelled (spurious) — reconnecting and retrying")
            consecutive_errors += 1
        except Exception as e:
            print(f"  ❌ dialog scan error: {e}")
            consecutive_errors += 1

        if consecutive_errors >= 3:
            print(f"  🔄 {consecutive_errors} consecutive errors — reconnecting Telegram client…")
            try:
                if client.is_connected():
                    await client.disconnect()
                await _interruptible_sleep(5)
                if _shutdown_event and _shutdown_event.is_set():
                    break
                await client.connect()
                if not await client.is_user_authorized():
                    print("  ❌ Session expired! Exiting.")
                    sys.exit(1)
                print("  ✅ Reconnected")
                consecutive_errors = 0
            except asyncio.CancelledError:
                if _shutdown_event and _shutdown_event.is_set():
                    break
                print("  ⚠️ reconnect cancelled (spurious) — will retry next cycle")
                consecutive_errors = 0  # reset so we don't loop on reconnect
            except Exception as e:
                print(f"  ❌ Reconnect failed: {e}")
                sys.exit(1)

        if total_new:
            print(f"  ✅ total new: {total_new}")
        else:
            print(f"  ✓ no new messages")

        write_snapshot()

        shutdown_requested = await _interruptible_sleep(SCAN_INTERVAL)
        if shutdown_requested:
            print("  🛑 shutdown signal received — exiting monitor loop cleanly")
            break

    print("🛑 monitor_loop exited")

# ── HTTP API ──────────────────────────────────────────────────────────────────
routes = web.RouteTableDef()

@routes.get("/messages")
async def api_messages(request):
    minutes    = int(request.rel_url.query.get("minutes", "60"))
    dialog_id  = request.rel_url.query.get("dialog")
    dtype      = request.rel_url.query.get("type")   # group / dm / channel
    limit      = int(request.rel_url.query.get("limit", "200"))
    msgs       = fetch_messages_db(minutes, dialog_id, dtype, limit)
    return web.json_response({"count": len(msgs), "minutes": minutes, "messages": msgs})

@routes.get("/dialogs")
async def api_dialogs(request):
    conn  = sqlite3.connect(DB_PATH)
    rows  = conn.execute(
        "SELECT DISTINCT dialog_id, dialog_name, dialog_type FROM messages"
    ).fetchall()
    conn.close()
    return web.json_response([
        {"id": r[0], "name": r[1], "type": r[2]} for r in rows
    ])

@routes.get("/groups")
async def api_groups(request):
    conn  = sqlite3.connect(DB_PATH)
    rows  = conn.execute(
        "SELECT DISTINCT dialog_id, dialog_name FROM messages WHERE dialog_type IN ('group','channel')"
    ).fetchall()
    conn.close()
    return web.json_response([{"id": r[0], "name": r[1]} for r in rows])

@routes.post("/send")
async def api_send(request):
    data = await request.json()
    chat = data.get("chat")
    text = data.get("text")
    reply_to = data.get("reply_to_msg_id")
    if chat is None or not text:
        return web.json_response({"error": "chat and text required"}, status=400)

    # Accept numeric dialog_id values like "-1002480957486" by resolving to an entity.
    # Telethon expects a username/peer/entity, not the raw dialog id string.
    try:
        target = chat
        if isinstance(chat, (int, float)) or (isinstance(chat, str) and chat.lstrip('-').isdigit()):
            dialog_id = int(chat)
            if str(dialog_id).startswith("-100"):
                target = PeerChannel(abs(dialog_id) - 1000000000000)
            else:
                target = PeerChat(abs(dialog_id))

        entity = await client.get_entity(target)
        kwargs = {}
        if reply_to:
            kwargs["reply_to"] = int(reply_to)
        await client.send_message(entity, text, **kwargs)
        return web.json_response({"ok": True, "sent_to": str(chat)})
    except Exception as e:
        return web.json_response({"error": str(e)}, status=500)

@routes.get("/status")
async def api_status(request):
    if not _telegram_ready:
        return web.json_response({"status": "starting"})
    conn  = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    by_type = conn.execute(
        "SELECT dialog_type, COUNT(*) FROM messages GROUP BY dialog_type"
    ).fetchall()
    conn.close()
    return web.json_response({
        "status":        "running",
        "total_messages": total,
        "by_type":       {r[0]: r[1] for r in by_type},
    })

async def start_api():
    """Start aiohttp API and keep this coroutine alive indefinitely.
    Previously this function returned immediately, which caused runner/site
    to go out of scope and become GC candidates — potentially triggering
    unexpected cleanup callbacks on the event loop.
    """
    app = web.Application()
    app.add_routes(routes)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", API_PORT)
    await site.start()
    print(f"🌐 HTTP API: http://127.0.0.1:{API_PORT}")
    # Keep coroutine alive so runner/site are NOT garbage collected.
    # Wait for shutdown signal (or external cancellation) before cleanup.
    try:
        if _shutdown_event is not None:
            await _shutdown_event.wait()
        else:
            await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass
    finally:
        await runner.cleanup()

# ── Entry point ───────────────────────────────────────────────────────────────
def _on_api_task_done(task: asyncio.Task) -> None:
    """Callback fired when the API background task ends unexpectedly."""
    if task.cancelled():
        print("⚠️ [api] background task was cancelled")
    elif task.exception():
        print(f"⚠️ [api] background task raised: {task.exception()}")
    else:
        print("ℹ️ [api] background task finished (unexpected)")


async def main():
    global _shutdown_event, _telegram_ready
    _shutdown_event = asyncio.Event()

    # ── Install SIGTERM / SIGINT handlers ───────────────────────────────────
    # We handle signals here (inside the async context) so we can set
    # _shutdown_event cleanly.  This replaces Python's default behaviour of
    # raising KeyboardInterrupt / CancelledError, which was causing pm2 to
    # record clean shutdowns as crashes (non-zero exit → restart loop).
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _on_signal, sig)

    init_db()

    # ── Start HTTP API FIRST — before Telegram init ─────────────────────────
    # CRITICAL: port 8765 must be up within 2-3s of process start so the
    # watchdog never sees connection-refused and triggers a SIGINT restart loop.
    # /status returns {"status":"starting"} until _telegram_ready is set True.
    #
    # CRITICAL: do NOT use asyncio.gather(start_api(), monitor_loop()).
    # If start_api() raises any exception (e.g. OSError: port in use on
    # rapid restart), gather() would cancel monitor_loop() — causing the
    # mysterious CancelledError crashes we were seeing.
    # By creating an independent Task, the API and monitor are fully decoupled.
    api_task = asyncio.create_task(start_api(), name="tg-api")
    api_task.add_done_callback(_on_api_task_done)
    # Yield briefly so aiohttp binds the port before we start the slower
    # Telegram handshake.  This ensures the watchdog sees HTTP 200 immediately.
    await asyncio.sleep(0.5)
    print("🌐 HTTP API ready — starting Telegram…")

    await client.start(phone=PHONE)
    print("✅ Telegram connected")
    print("📋 Loading dialogs…")
    # Use get_dialogs() (returns a list) to avoid _DialogsIter cleanup bugs on cancellation
    await client.get_dialogs(limit=None)
    print("✅ Dialogs loaded")

    # Mark fully ready — /status now returns {"status":"running"}
    _telegram_ready = True

    try:
        await monitor_loop()
    finally:
        # Clean up API task on exit
        if not api_task.done():
            api_task.cancel()
        try:
            await api_task
        except (asyncio.CancelledError, Exception):
            pass
        # Disconnect Telegram client cleanly
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass
        print("✅ tg-monitor shutdown complete")


def _on_signal(sig: signal.Signals) -> None:
    """Signal handler called by the event loop on SIGTERM / SIGINT."""
    print(f"\n🛑 Received {sig.name} — requesting graceful shutdown…")
    if _shutdown_event is not None:
        _shutdown_event.set()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        # KeyboardInterrupt can still arrive in rare edge cases (e.g. second
        # SIGINT before signal handler is installed).  Treat as clean exit.
        pass
