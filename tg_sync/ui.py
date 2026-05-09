"""Local read-only browser UI for Telegram Monitor.

The MVP intentionally uses the Python standard library so it can run in the
repo's lightweight operator environment without adding a UI framework.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .api_client import ApiClient, ApiUnavailable
from .config import DEFAULT_API_URL, DEFAULT_UI_HOST, DEFAULT_UI_PORT, clamp_limit, resolve_db_path
from .store import ReadOnlyStore, thread_to_markdown

RUNTIME_NOTE = (
    "Source repo: /Users/giga/projects/openclaw/tg-monitor. "
    "Live runtime until cutover: /Users/giga/.openclaw/workspace/tg-monitor."
)


@dataclass(frozen=True)
class _Resource:
    canonical: str


@dataclass(frozen=True)
class _Route:
    resource: _Resource
    handler: Callable[[dict[str, str]], tuple[int, str, str, dict[str, str]]]


class _Router:
    def __init__(self) -> None:
        self._routes: list[_Route] = []

    def add_get(self, path: str, handler: Callable[[dict[str, str]], tuple[int, str, str, dict[str, str]]]) -> None:
        self._routes.append(_Route(_Resource(path), handler))

    def routes(self) -> list[_Route]:
        return list(self._routes)

    def match(self, path: str) -> _Route | None:
        return next((route for route in self._routes if route.resource.canonical == path), None)


class LocalUiApp:
    def __init__(self, api_url: str, db_path: str | Path):
        self.api_url = api_url.rstrip("/")
        self.db_path = str(db_path)
        self.router = _Router()


def render_index_html(api_url: str, db_path: str | Path) -> str:
    safe_state = {"apiUrl": api_url, "dbPath": str(db_path)}
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Telegram Sync — Read-only</title>
  <style>
    :root {{ color-scheme: light dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; }}
    body {{ margin: 0; background: #0f172a; color: #e5e7eb; }}
    header {{ padding: 18px 24px; background: #111827; border-bottom: 1px solid #334155; position: sticky; top: 0; z-index: 2; }}
    main {{ display: grid; grid-template-columns: 360px 1fr; gap: 16px; padding: 16px; }}
    section {{ background: #111827; border: 1px solid #334155; border-radius: 14px; padding: 14px; }}
    .banner {{ background: #422006; border: 1px solid #f59e0b; color: #fef3c7; padding: 10px 12px; border-radius: 12px; margin-top: 10px; }}
    input, select, button, textarea {{ background: #020617; color: #e5e7eb; border: 1px solid #475569; border-radius: 8px; padding: 8px; }}
    button:disabled, textarea:disabled {{ opacity: .5; cursor: not-allowed; }}
    table {{ width: 100%; border-collapse: collapse; font-size: 13px; }} th, td {{ border-bottom: 1px solid #334155; padding: 8px; text-align: left; vertical-align: top; }}
    .muted {{ color: #94a3b8; font-size: 12px; }} .row {{ display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }}
    .messages {{ display: grid; gap: 8px; }} .message {{ border: 1px solid #334155; border-radius: 10px; padding: 10px; background: #0b1120; }}
    pre {{ white-space: pre-wrap; overflow-wrap: anywhere; background: #020617; border-radius: 10px; padding: 10px; }}
  </style>
</head>
<body>
  <header>
    <h1>Telegram Sync local UI</h1>
    <div class="muted">API: <code>{api_url}</code> · DB: <code>{db_path}</code></div>
    <div class="banner"><strong>Read-only mode:</strong> Telegram sends/replies/deletes are disabled. Existing <code>POST /send</code> is manual-gated and not used by this UI.</div>
    <div class="muted">{RUNTIME_NOTE}</div>
  </header>
  <main>
    <aside>
      <section><h2>Health/session</h2><button onclick="loadStatus()">Refresh status</button><button onclick="navigator.clipboard?.writeText('curl -fsS {api_url}/status')">Copy safe smoke command</button><pre id="status">Loading…</pre></section>
      <section style="margin-top:16px"><h2>Dialogs</h2><div class="row"><select id="dialogType"><option value="">all</option><option>group</option><option>channel</option><option>dm</option></select><input id="dialogQuery" placeholder="name/id filter" /><input id="minCount" type="number" min="0" value="0" style="width:84px" /><button onclick="loadDialogs()">Load</button></div><div id="dialogs" class="muted">No dialogs loaded yet.</div></section>
      <section style="margin-top:16px"><h2>Manual-gated future action</h2><textarea disabled rows="4" style="width:100%" placeholder="Compose/reply is intentionally disabled in O-71 MVP."></textarea><button disabled>Send disabled</button><button disabled>Reply disabled</button><button disabled>Delete disabled</button></section>
    </aside>
    <div>
      <section><h2>Search / recent messages</h2><div class="row"><input id="searchQuery" placeholder="search text" /><input id="dialogId" placeholder="dialog id" /><select id="messageType"><option value="">all types</option><option>group</option><option>channel</option><option>dm</option></select><input id="sender" placeholder="sender" /><input id="limit" type="number" min="1" max="1000" value="25" style="width:84px" /><button onclick="runSearch()">Search DB</button><button onclick="loadRecent()">Recent</button></div><div id="messages" class="messages"></div></section>
      <section style="margin-top:16px"><h2>Thread / export</h2><div class="row"><input id="threadDialog" placeholder="dialog id" /><input id="threadMessage" type="number" placeholder="message id" /><input id="threadContext" type="number" value="10" style="width:84px" /><button onclick="loadThread()">Open thread</button><button onclick="downloadThread('markdown')">Export Markdown</button><button onclick="downloadThread('json')">Export JSON</button></div><pre id="thread">Local DB-derived thread output appears here.</pre></section>
    </div>
  </main>
<script>
const STATE = {json.dumps(safe_state)};
function qs(id) {{ return document.getElementById(id); }}
function params(obj) {{ return new URLSearchParams(Object.entries(obj).filter(([,v]) => v !== '' && v != null)); }}
async function getJson(url) {{ const res = await fetch(url); const data = await res.json(); if (!res.ok) throw new Error(data.error || res.statusText); return data; }}
function esc(s) {{ return String(s ?? '').replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[c])); }}
async function loadStatus() {{ try {{ qs('status').textContent = JSON.stringify(await getJson('/api/status'), null, 2); }} catch(e) {{ qs('status').textContent = 'ERROR: '+e.message; }} }}
async function loadDialogs() {{ const data = await getJson('/api/dialogs?' + params({{type: qs('dialogType').value, query: qs('dialogQuery').value, min_count: qs('minCount').value}})); qs('dialogs').innerHTML = '<table><tr><th>Name</th><th>ID</th><th>Type</th><th>Count</th><th>Latest</th></tr>' + data.dialogs.map(d => `<tr onclick="selectDialog('${{esc(d.id)}}')"><td>${{esc(d.name)}}</td><td><code>${{esc(d.id)}}</code></td><td>${{esc(d.type)}}</td><td>${{esc(d.message_count)}}</td><td>${{esc(d.latest_date)}}</td></tr>`).join('') + '</table>'; }}
function selectDialog(id) {{ qs('dialogId').value = id; qs('threadDialog').value = id; loadRecent(); }}
function renderMessages(data) {{ qs('messages').innerHTML = data.messages.map(m => `<div class="message"><div class="muted">${{esc(m.date)}} · ${{esc(m.dialog)}} · msg ${{esc(m.msg_id)}} · reply_to ${{esc(m.reply_to_id)}}</div><strong>${{esc(m.sender)}}</strong><p>${{esc(m.text || '[redacted]')}}</p><button onclick="openThread('${{esc(m.dialog_id)}}', '${{esc(m.msg_id)}}')">Open thread</button><button onclick="navigator.clipboard?.writeText('tg-monitor://dialog/${{esc(m.dialog_id)}}/message/${{esc(m.msg_id)}}')">Copy ref</button></div>`).join(''); }}
async function runSearch() {{ renderMessages(await getJson('/api/search?' + params({{q: qs('searchQuery').value, dialog: qs('dialogId').value, type: qs('messageType').value, sender: qs('sender').value, limit: qs('limit').value}}))); }}
async function loadRecent() {{ renderMessages(await getJson('/api/recent?' + params({{dialog: qs('dialogId').value, type: qs('messageType').value, sender: qs('sender').value, limit: qs('limit').value}}))); }}
function openThread(dialog, msg) {{ qs('threadDialog').value = dialog; qs('threadMessage').value = msg; loadThread(); }}
async function loadThread() {{ qs('thread').textContent = JSON.stringify(await getJson('/api/thread?' + params({{dialog: qs('threadDialog').value, message_id: qs('threadMessage').value, context: qs('threadContext').value}})), null, 2); }}
function downloadThread(format) {{ location.href = '/api/export/thread?' + params({{dialog: qs('threadDialog').value, message_id: qs('threadMessage').value, context: qs('threadContext').value, format}}); }}
const initial = new URLSearchParams(location.search);
if (initial.get('dialog')) {{ qs('dialogId').value = initial.get('dialog'); qs('threadDialog').value = initial.get('dialog'); }}
if (initial.get('q')) qs('searchQuery').value = initial.get('q');
if (initial.get('type')) {{ qs('messageType').value = initial.get('type'); qs('dialogType').value = initial.get('type'); }}
if (initial.get('thread_dialog')) qs('threadDialog').value = initial.get('thread_dialog');
if (initial.get('thread_message')) qs('threadMessage').value = initial.get('thread_message');
if (initial.get('limit')) qs('limit').value = initial.get('limit');
loadStatus();
loadDialogs().then(() => {{
  if (initial.get('q')) runSearch();
  if (initial.get('recent')) loadRecent();
  if (initial.get('thread_dialog') && initial.get('thread_message')) loadThread();
}});
</script>
</body></html>"""


def create_app(api_url: str = DEFAULT_API_URL, db_path: str | Path | None = None) -> LocalUiApp:
    app = LocalUiApp(api_url=api_url, db_path=resolve_db_path(str(db_path) if db_path else None))

    def index(_query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        return 200, "text/html; charset=utf-8", render_index_html(app.api_url, app.db_path), {}

    def api_status(_query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        try:
            payload = ApiClient(app.api_url).status()
            payload = {"ok": True, "api_url": app.api_url, "db_path": app.db_path, "checked_at": datetime.now(timezone.utc).isoformat(), **payload}
        except ApiUnavailable as exc:
            payload = {"ok": False, "api_url": app.api_url, "db_path": app.db_path, "error": str(exc)}
        return _json(payload)

    def api_dialogs(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        dialogs = ReadOnlyStore(app.db_path).list_dialogs(
            dialog_type=query.get("type") or None,
            query=query.get("query") or None,
            min_count=int(query.get("min_count", "0") or 0),
            limit=clamp_limit(query.get("limit"), default=200),
        )
        return _json({"ok": True, "source": "sqlite-readonly", "count": len(dialogs), "dialogs": dialogs})

    def api_recent(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        messages = ReadOnlyStore(app.db_path).recent_messages(
            minutes=int(query.get("minutes", "100000000") or 100000000),
            dialog_id=query.get("dialog") or None,
            dialog_type=query.get("type") or None,
            sender=query.get("sender") or None,
            limit=clamp_limit(query.get("limit"), default=25),
        )
        return _json({"ok": True, "source": "sqlite-readonly", "count": len(messages), "messages": messages})

    def api_search(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        q = query.get("q", "")
        if not q:
            return _json({"ok": True, "source": "sqlite-readonly", "count": 0, "messages": []})
        messages = ReadOnlyStore(app.db_path).search_messages(
            q,
            dialog_id=query.get("dialog") or None,
            dialog_type=query.get("type") or None,
            sender=query.get("sender") or None,
            since=query.get("since") or None,
            until=query.get("until") or None,
            limit=clamp_limit(query.get("limit"), default=25),
        )
        return _json({"ok": True, "source": "sqlite-readonly", "count": len(messages), "messages": messages})

    def api_thread(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        thread = ReadOnlyStore(app.db_path).get_thread(query["dialog"], int(query["message_id"]), context=int(query.get("context", "10") or 10), max_depth=int(query.get("max_depth", "20") or 20))
        return _json({"ok": True, **thread})

    def api_export_thread(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        thread = ReadOnlyStore(app.db_path).get_thread(query["dialog"], int(query["message_id"]), context=int(query.get("context", "10") or 10), max_depth=int(query.get("max_depth", "20") or 20))
        if query.get("format") == "json":
            return 200, "application/json", json.dumps(thread, ensure_ascii=False, indent=2) + "\n", {"Content-Disposition": "attachment; filename=tg-thread.json"}
        return 200, "text/markdown; charset=utf-8", thread_to_markdown(thread), {"Content-Disposition": "attachment; filename=tg-thread.md"}

    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/dialogs", api_dialogs)
    app.router.add_get("/api/recent", api_recent)
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/thread", api_thread)
    app.router.add_get("/api/export/thread", api_export_thread)
    return app


def _json(payload: dict[str, Any], status: int = 200) -> tuple[int, str, str, dict[str, str]]:
    return status, "application/json", json.dumps(payload, ensure_ascii=False, indent=2) + "\n", {}


def _query_dict(raw_query: str) -> dict[str, str]:
    parsed = parse_qs(raw_query, keep_blank_values=False)
    return {key: values[-1] for key, values in parsed.items()}


def serve(app: LocalUiApp, host: str, port: int) -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - stdlib API
            parsed = urlparse(self.path)
            route = app.router.match(parsed.path)
            if route is None:
                self._send(404, "application/json", json.dumps({"ok": False, "error": "not found"}), {})
                return
            try:
                status, content_type, body, headers = route.handler(_query_dict(parsed.query))
            except Exception as exc:
                status, content_type, body, headers = _json({"ok": False, "error": str(exc)}, status=500)
            self._send(status, content_type, body, headers)

        def log_message(self, _format: str, *_args: Any) -> None:
            return

        def _send(self, status: int, content_type: str, body: str, headers: dict[str, str]) -> None:
            data = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            for key, value in headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(data)

    server = ThreadingHTTPServer((host, port), Handler)
    try:
        print(f"Read-only Telegram Sync UI: http://{host}:{port}")
        server.serve_forever()
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the read-only Telegram Sync local UI")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--db", default=None)
    parser.add_argument("--host", default=DEFAULT_UI_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"}:
        print("Refusing to bind non-localhost host in read-only MVP", file=sys.stderr)
        return 2
    serve(create_app(args.api_url, args.db), args.host, args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
