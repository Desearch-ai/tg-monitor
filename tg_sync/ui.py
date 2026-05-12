"""Birdclaw-style local read-only workspace for Telegram Monitor.

The O-71 UI deliberately stays Python-served: this repo has no Node toolchain,
and the operator surface must run in the same lightweight localhost environment
as the existing Telethon monitor.  The browser app is still structured as a
workspace with lanes instead of a debug/form page.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from .accounts import AccountRegistry
from .api_client import ApiClient, ApiUnavailable
from .backfill import MAX_BACKFILL_LIMIT
from .config import DEFAULT_API_URL, DEFAULT_UI_HOST, DEFAULT_UI_PORT, clamp_limit, resolve_db_path
from .store import DBUnavailable, ReadOnlyStore, thread_to_markdown

RUNTIME_NOTE = (
    "Source repo: /Users/giga/projects/openclaw/tg-monitor. "
    "Live runtime until cutover: /Users/giga/.openclaw/workspace/tg-monitor."
)
STALE_AFTER_MINUTES = 120


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
  <title>Telegram Sync — Read-only local workspace</title>
  <style>
    :root {{ color-scheme: dark; font-family: Inter, ui-sans-serif, system-ui, -apple-system, sans-serif; --bg:#08111f; --panel:#0d1829; --panel2:#111f33; --line:#23364f; --text:#e8eef8; --muted:#8ea3bd; --accent:#7dd3fc; --good:#34d399; --warn:#f59e0b; --bad:#fb7185; }}
    * {{ box-sizing: border-box; }} body {{ margin:0; background:linear-gradient(135deg,#07111f,#101827 48%,#0b1120); color:var(--text); }}
    button,input,select {{ font:inherit; }} button {{ cursor:pointer; background:#14243a; color:var(--text); border:1px solid #315071; border-radius:10px; padding:9px 12px; }} button:hover {{ border-color:var(--accent); }}
    input,select {{ background:#07111f; color:var(--text); border:1px solid #2b425f; border-radius:10px; padding:9px 10px; min-width:0; }}
    .shell {{ min-height:100vh; display:grid; grid-template-columns:260px 1fr; }}
    .sidebar {{ border-right:1px solid var(--line); background:rgba(8,17,31,.92); padding:18px; position:sticky; top:0; height:100vh; }}
    .brand {{ display:grid; gap:5px; margin-bottom:18px; }} .brand h1 {{ margin:0; font-size:20px; letter-spacing:-.02em; }} .brand small,.muted {{ color:var(--muted); }}
    .readonly {{ border:1px solid rgba(125,211,252,.45); background:rgba(14,165,233,.08); border-radius:14px; padding:12px; font-size:13px; line-height:1.4; margin:14px 0; }}
    .nav {{ display:grid; gap:8px; margin-top:14px; }} .nav button {{ width:100%; text-align:left; background:transparent; border-color:transparent; }} .nav button.active {{ background:#11243d; border-color:#365d84; }}
    .content {{ padding:20px; }} .topbar {{ display:flex; justify-content:space-between; gap:12px; align-items:flex-start; margin-bottom:16px; }}
    .lane {{ display:none; }} .lane.active {{ display:block; }} .lane-title {{ margin:0 0 4px; font-size:28px; letter-spacing:-.03em; }}
    .grid {{ display:grid; gap:14px; }} .grid.cols-2 {{ grid-template-columns: minmax(0,1fr) minmax(0,1fr); }} .grid.cols-3 {{ grid-template-columns: repeat(3,minmax(0,1fr)); }}
    .card {{ background:rgba(13,24,41,.88); border:1px solid var(--line); border-radius:18px; padding:16px; box-shadow:0 18px 50px rgba(0,0,0,.18); }} .card h2,.card h3 {{ margin:0 0 10px; }}
    .metric {{ font-size:28px; font-weight:750; letter-spacing:-.03em; }} .pill {{ display:inline-flex; align-items:center; gap:6px; border:1px solid #315071; border-radius:999px; padding:4px 9px; color:#cfe5ff; background:#0a1627; font-size:12px; }} .pill.good {{ border-color:#166534; color:#bbf7d0; }} .pill.warn {{ border-color:#92400e; color:#fde68a; }} .pill.bad {{ border-color:#9f1239; color:#fecdd3; }}
    .actions,.filters {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; }} .filters > input,.filters > select {{ flex:1 1 150px; }}
    .workspace {{ display:grid; grid-template-columns:360px 1fr; gap:14px; min-height:620px; }} .list {{ display:grid; gap:8px; max-height:68vh; overflow:auto; padding-right:4px; }}
    .source,.message,.thread-block {{ border:1px solid var(--line); border-radius:14px; padding:12px; background:#091524; }} .source {{ cursor:pointer; }} .source.active {{ outline:2px solid rgba(125,211,252,.45); }}
    .message {{ display:grid; gap:7px; }} .message p {{ margin:0; color:#d7e3f3; line-height:1.45; }} .meta {{ color:var(--muted); font-size:12px; display:flex; flex-wrap:wrap; gap:8px; }}
    table {{ width:100%; border-collapse:collapse; }} th,td {{ text-align:left; border-bottom:1px solid var(--line); padding:9px; vertical-align:top; }} th {{ color:#aac0dc; font-size:12px; text-transform:uppercase; letter-spacing:.04em; }}
    pre {{ white-space:pre-wrap; overflow-wrap:anywhere; background:#06101d; border:1px solid var(--line); border-radius:14px; padding:12px; max-height:420px; overflow:auto; }}
    code {{ color:#bae6fd; }} .empty {{ border:1px dashed #2c4565; border-radius:14px; padding:18px; color:var(--muted); text-align:center; }}
    .notice {{ border:1px solid #2b425f; border-radius:14px; padding:10px 12px; background:#091524; color:#cfe5ff; font-size:13px; line-height:1.4; }}
    .notice.bad {{ border-color:#9f1239; color:#fecdd3; }} .notice.warn {{ border-color:#92400e; color:#fde68a; }} .notice.good {{ border-color:#166534; color:#bbf7d0; }}
    @media (max-width: 980px) {{ .shell,.workspace,.grid.cols-2,.grid.cols-3 {{ grid-template-columns:1fr; }} .sidebar {{ position:relative; height:auto; }} }}
  </style>
</head>
<body>
<div class="shell">
  <aside class="sidebar">
    <div class="brand"><h1>Telegram Sync</h1><small>birdclaw-style local operator workspace</small></div>
    <div class="readonly"><strong>Read-only local workspace.</strong><br/>This O-71 surface only reads the localhost API and SQLite DB. It provides no Telegram send, reply, delete, or compose controls.</div>
    <div class="muted">API <code>{api_url}</code><br/>DB <code>{db_path}</code></div>
    <div class="readonly" id="accountPanel"><strong>Account</strong><br/><span id="accountStatus">Single-account mode until account registry is configured.</span><select id="accountSelector" onchange="setActiveAccount(this.value)" style="width:100%; margin-top:8px"><option value="default">Default account</option></select></div>
    <nav class="nav" aria-label="workspace lanes">
      <button class="active" data-lane="home" onclick="showLane('home')">Home / Sync Dashboard</button>
      <button data-lane="chats" onclick="showLane('chats')">Chats / Sources</button>
      <button data-lane="search" onclick="showLane('search')">Search / Research</button>
      <button data-lane="thread" onclick="showLane('thread')">Thread / Export</button>
    </nav>
  </aside>
  <main class="content">
    <div class="topbar">
      <div><h1 class="lane-title" id="laneTitle">Home / Sync Dashboard</h1><div class="muted">{RUNTIME_NOTE}</div><div class="muted">Internal review: default bind is localhost. For Tailscale use explicit <code>--host 0.0.0.0 --allow-internal-bind</code>; this UI is read-only and renders no secrets/session paths.</div></div>
      <div class="actions"><button onclick="refreshAll()">Refresh workspace</button><button onclick="copyStatusJson()">Copy JSON status</button></div>
    </div>

    <section id="lane-home" class="lane active">
      <div class="grid cols-3" id="homeMetrics"></div>
      <div class="grid cols-2" style="margin-top:14px">
        <div class="card"><h2>Watched / known sources</h2><div id="watchlist" class="list"></div></div>
        <div class="card"><h2>Recent activity</h2><div id="recentActivity" class="list"></div></div>
      </div>
      <div class="card" style="margin-top:14px"><h2>Primary actions</h2><div class="actions"><button onclick="showLane('search'); qs('searchQuery').focus()">Search</button><button onclick="showLane('chats')">Open chats</button><button onclick="exportRecentContext()">Export recent context</button><button onclick="copyStatusJson()">Copy JSON status</button></div></div>
    </section>

    <section id="lane-chats" class="lane">
      <div class="workspace">
        <div class="card"><h2>Chats / Sources</h2><div class="filters"><select id="sourceScope"><option value="all">all</option><option value="watched">watched</option><option value="group">groups</option><option value="channel">channels</option><option value="dm">DMs</option></select><input id="sourceQuery" placeholder="name or id"/><select id="sourceSort"><option value="recent">sort recent</option><option value="count">sort count</option><option value="stale">sort stale</option></select><button onclick="loadDialogs()">Apply</button></div><div id="dialogs" class="list" style="margin-top:12px"></div></div>
        <div class="card"><div id="selectedChatHeader" class="empty">Choose a source to inspect local messages.</div><div class="actions" style="margin:12px 0"><button onclick="loadSelectedMessages()">Load latest local messages</button><button onclick="showLane('search'); runSearchForSelected()">Search in source</button></div><div class="notice"><strong>Older local history</strong><br/>Use bounded cursors so this panel does not imply only the newest messages exist. Load older local messages from SQLite, or use Historical sync dry-run copy when local history is incomplete.</div><div class="filters" style="margin:10px 0"><input id="olderBeforeId" type="number" min="1" placeholder="before msg id"/><input id="olderBeforeDate" placeholder="before ISO/date"/><input id="olderLimit" type="number" min="1" max="1000" value="50"/><button onclick="loadOlderMessages()">Load older local messages</button><button onclick="copyBackfillPlan()">Historical sync dry-run</button></div><div id="historyStatus" class="muted">Older fetches are read-only and capped at {MAX_BACKFILL_LIMIT}.</div><div id="chatMessages" class="list" style="margin-top:10px"></div></div>
      </div>
    </section>

    <section id="lane-search" class="lane">
      <div class="card"><h2>Search / Research</h2><p class="muted">Local SQLite search — not a live Telegram query. Filters only inspect stored monitor data.</p><div id="searchStatus" class="notice">Enter a query to search the real local SQLite DB. Empty, bad DB, and no-match states are shown here instead of blank panels.</div><div class="filters" style="margin-top:10px"><input id="searchQuery" placeholder="query text"/><input id="searchDialog" placeholder="source/chat id"/><select id="searchType"><option value="">all types</option><option value="group">group</option><option value="channel">channel</option><option value="dm">dm</option></select><input id="searchSender" placeholder="sender"/><input id="searchMinutes" type="number" min="1" placeholder="recency minutes"/><input id="searchSince" placeholder="since ISO/date"/><input id="searchUntil" placeholder="until ISO/date"/><input id="searchLimit" type="number" min="1" max="1000" value="25"/><button onclick="runSearch()">Search DB</button><button onclick="copySearch('json')">Copy JSON</button><button onclick="copySearch('markdown')">Copy Markdown</button></div></div>
      <div class="card" style="margin-top:14px"><h2>Results</h2><div id="searchResults" class="list"></div></div>
    </section>

    <section id="lane-thread" class="lane">
      <div class="grid cols-2">
        <div class="card"><h2>Thread / Export Inspector</h2><div class="filters"><input id="threadDialog" placeholder="dialog id"/><input id="threadMessage" type="number" placeholder="message id"/><input id="threadContext" type="number" min="1" max="100" value="10"/><label class="pill"><input id="metadataOnly" type="checkbox"/> metadata only</label><button onclick="loadThread()">Open thread</button></div><div class="actions" style="margin-top:10px"><button onclick="downloadThread('markdown')">Export Markdown</button><button onclick="downloadThread('json')">Export JSON</button><button onclick="copyLocalRef()">Copy local ref</button></div><div id="exportSummary" style="margin-top:12px"></div></div>
        <div class="card"><h2>Export preview</h2><pre id="threadPreview">Open a message thread from Chats or Search.</pre></div>
      </div>
      <div class="card" style="margin-top:14px"><h2>Thread structure</h2><div id="threadBlocks" class="grid"></div></div>
    </section>
  </main>
</div>
<script>
const STATE = {{...{json.dumps(safe_state)}, status: null, dashboard: null, dialogs: [], selectedDialog: null, messages: [], searchResults: [], thread: null, accounts: [], activeAccount: "default"}};
function qs(id) {{ return document.getElementById(id); }}
function params(obj) {{ return new URLSearchParams(Object.entries(obj).filter(([,v]) => v !== '' && v != null && v !== false)); }}
function esc(s) {{ return String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c])); }}
async function getJson(url) {{ const res = await fetch(url); const data = await res.json(); if (!res.ok) {{ const err = new Error(data.error || res.statusText); err.payload = data; throw err; }} return data; }}
function accountParam() {{ return STATE.activeAccount ? {{account: STATE.activeAccount}} : {{}}; }}
function setNotice(id, message, kind='') {{ const el = qs(id); if (!el) return; el.className = 'notice' + (kind ? ' ' + kind : ''); el.innerHTML = message; }}
function showLane(name) {{ document.querySelectorAll('.lane').forEach(el => el.classList.remove('active')); qs('lane-' + name).classList.add('active'); document.querySelectorAll('.nav button').forEach(b => b.classList.toggle('active', b.dataset.lane === name)); qs('laneTitle').textContent = {{home:'Home / Sync Dashboard', chats:'Chats / Sources', search:'Search / Research', thread:'Thread / Export'}}[name]; }}
function pill(state) {{ const cls = state === 'fresh' ? 'good' : state === 'stale' ? 'warn' : state === 'error' ? 'bad' : ''; return `<span class="pill ${{cls}}">${{esc(state || 'unknown')}}</span>`; }}
function metric(label, value, sub='') {{ return `<div class="card"><div class="muted">${{esc(label)}}</div><div class="metric">${{esc(value)}}</div>${{sub ? `<div class="muted">${{esc(sub)}}</div>` : ''}}</div>`; }}
async function refreshAll() {{ await loadAccounts(); await loadDashboard(); await loadDialogs(); }}
async function loadAccounts() {{ try {{ const data = await getJson('/api/accounts'); STATE.accounts = data.accounts || []; STATE.activeAccount = data.active_account || STATE.accounts.find(a => a.active)?.id || 'default'; renderAccountPanel(data); }} catch (err) {{ STATE.accounts = [{{id:'default', label:'Default account', active:true}}]; STATE.activeAccount = 'default'; renderAccountPanel({{mode:'single', accounts: STATE.accounts, active_account:'default'}}); }} }}
function renderAccountPanel(data) {{ const accounts = STATE.accounts.length ? STATE.accounts : [{{id:'default', label:'Default account'}}]; qs('accountSelector').innerHTML = accounts.map(a => `<option value="${{esc(a.id)}}" ${{a.id === STATE.activeAccount ? 'selected' : ''}}>${{esc(a.label || a.id)}}</option>`).join(''); const mode = accounts.length > 1 ? `${{accounts.length}} accounts available` : 'Single-account mode'; qs('accountStatus').innerHTML = `${{esc(mode)}} · active <code>${{esc(STATE.activeAccount)}}</code><br/><span class="muted">No secrets or session paths are rendered.</span>`; }}
function setActiveAccount(id) {{ STATE.activeAccount = id || 'default'; refreshAll(); }}
async function loadDashboard() {{ const data = await getJson('/api/dashboard?' + params(accountParam())); STATE.dashboard = data; STATE.status = data.api_status; renderHome(data); }}
function renderHome(data) {{ const s = data.store || {{}}; const api = data.api_status || {{}}; const byType = Object.entries(s.by_type || api.by_type || {{}}).map(([k,v]) => `${{k}} ${{v}}`).join(' · ') || 'no type counts'; qs('homeMetrics').innerHTML = metric('API status', api.telegram_status || (api.ok ? 'ok' : 'unavailable'), `ready: ${{api.telegram_ready ?? 'unknown'}}`) + metric('Local messages', s.total_messages ?? api.total_messages ?? '—', byType) + metric('Freshness', s.freshness?.state || 'unknown', s.latest_message_at || 'no latest message') + metric('DB path', data.db_path, 'SQLite read-only') + metric('Sources', s.source_count ?? '—', `${{(data.watched_sources || []).length}} watched/configured`) + metric('Safety', 'read-only', 'no Telegram write controls'); const watched = data.watched_sources?.length ? data.watched_sources : (s.sources || []); qs('watchlist').innerHTML = watched.length ? watched.map(d => sourceCard(d, false)).join('') : '<div class="empty">No configured watchlist found. Showing all known non-DM sources when available.</div>'; qs('recentActivity').innerHTML = (s.recent_activity || []).length ? s.recent_activity.map(messageCard).join('') : '<div class="empty">No recent local activity available.</div>'; }}
function sourceCard(d, selectable=true) {{ const active = STATE.selectedDialog && String(STATE.selectedDialog.id) === String(d.id); return `<div class="source ${{active ? 'active' : ''}}" ${{selectable ? `onclick="selectDialog('${{esc(d.id)}}')"` : ''}}><strong>${{esc(d.name || d.dialog_name || d.id)}}</strong><div class="meta"><span>${{esc(d.type || d.dialog_type || 'source')}}</span><span><code>${{esc(d.id || d.dialog_id)}}</code></span><span>${{esc(d.message_count ?? '')}} msgs</span><span>${{esc(d.latest_date || '')}}</span></div></div>`; }}
async function loadDialogs() {{ const scope = qs('sourceScope')?.value || 'all'; const data = await getJson('/api/dialogs?' + params({{...accountParam(), scope, query: qs('sourceQuery')?.value || '', sort: qs('sourceSort')?.value || 'recent', limit: 300}})); STATE.dialogs = data.dialogs || []; qs('dialogs').innerHTML = STATE.dialogs.length ? STATE.dialogs.map(d => sourceCard(d)).join('') : '<div class="empty">No sources match the filters.</div>'; }}
function selectDialog(id) {{ STATE.selectedDialog = STATE.dialogs.find(d => String(d.id) === String(id)) || {{id}}; qs('searchDialog').value = id; qs('threadDialog').value = id; renderSelectedHeader(); loadSelectedMessages(); }}
function renderSelectedHeader() {{ const d = STATE.selectedDialog || {{}}; qs('selectedChatHeader').className = ''; qs('selectedChatHeader').innerHTML = `<h2>${{esc(d.name || d.id)}}</h2><div class="meta"><span>${{esc(d.type || 'source')}}</span><span><code>${{esc(d.id)}}</code></span><span>${{esc(d.message_count ?? '—')}} local messages</span><span>latest ${{esc(d.latest_date || 'unknown')}}</span></div>`; }}
async function loadSelectedMessages() {{ if (!STATE.selectedDialog?.id) return; const data = await getJson('/api/recent?' + params({{...accountParam(), dialog: STATE.selectedDialog.id, limit: 50}})); STATE.messages = data.messages || []; qs('historyStatus').textContent = `${{data.count || 0}} latest local messages loaded. Use the older controls for earlier SQLite rows.`; renderChatMessages(); }}
function renderChatMessages() {{ qs('chatMessages').innerHTML = STATE.messages.length ? STATE.messages.map(messageCard).join('') : '<div class="empty">No local messages found for this source. If the DB is sparse, run a bounded Historical sync dry-run first.</div>'; }}
async function loadOlderMessages() {{ if (!STATE.selectedDialog?.id) return; const last = STATE.messages[STATE.messages.length - 1] || {{}}; const beforeId = qs('olderBeforeId').value || last.msg_id || ''; const data = await getJson('/api/recent?' + params({{...accountParam(), dialog: STATE.selectedDialog.id, before_id: beforeId, before_date: qs('olderBeforeDate').value, limit: qs('olderLimit').value || 50}})); STATE.messages = [...STATE.messages, ...(data.messages || [])]; qs('historyStatus').textContent = `${{data.count || 0}} older local messages loaded before msg ${{data.older_cursor?.before_id || 'date cursor'}}.`; renderChatMessages(); }}
function copyBackfillPlan() {{ const dialog = STATE.selectedDialog?.id || qs('searchDialog').value || '<dialog_id>'; const beforeId = qs('olderBeforeId').value || (STATE.messages[STATE.messages.length - 1]?.msg_id ?? '<older_than_msg_id>'); const limit = Math.min(Number(qs('olderLimit').value || 100), 1000); const cmd = `uv run python -m tg_sync.cli sync backfill --account ${{STATE.activeAccount || 'default'}} --dialog ${{dialog}} --limit ${{limit}} --before-id ${{beforeId}} --dry-run --json`; navigator.clipboard?.writeText(cmd); qs('historyStatus').textContent = 'Copied bounded Historical sync dry-run command. Run manually before any live backfill.'; }}
function messageCard(m) {{ const ref = `tg-monitor://dialog/${{m.dialog_id}}/message/${{m.msg_id}}`; return `<div class="message"><div class="meta"><span>${{esc(m.date)}}</span><span>${{esc(m.dialog)}}</span><span>${{esc(m.type)}}</span><span>msg ${{esc(m.msg_id)}}</span>${{m.reply_to_id ? `<span>↩ ${{esc(m.reply_to_id)}}</span>` : ''}}</div><strong>${{esc(m.sender || 'unknown')}}</strong><p>${{esc(m.text || '[metadata only]')}}</p><div class="actions"><button onclick="openThread('${{esc(m.dialog_id)}}','${{esc(m.msg_id)}}')">Open thread</button><button onclick="navigator.clipboard?.writeText('${{esc(ref)}}')">Copy ref</button></div></div>`; }}
function runSearchForSelected() {{ qs('searchDialog').value = STATE.selectedDialog?.id || ''; qs('searchQuery').focus(); }}
async function runSearch() {{ const query = qs('searchQuery').value.trim(); if (!query) {{ STATE.searchResults = []; setNotice('searchStatus', 'Enter a query to search stored Telegram messages.', 'warn'); renderSearchResults({{empty_reason:'missing_query'}}); return; }} setNotice('searchStatus', 'Searching local SQLite…'); try {{ const data = await getJson('/api/search?' + params({{...accountParam(), q: query, dialog: qs('searchDialog').value, type: qs('searchType').value, sender: qs('searchSender').value, minutes: qs('searchMinutes').value, since: qs('searchSince').value, until: qs('searchUntil').value, limit: qs('searchLimit').value}})); STATE.searchResults = data.messages || []; setNotice('searchStatus', data.count ? `${{data.count}} local result(s) for “${{esc(data.query)}}”.` : `No local SQLite matches for “${{esc(data.query)}}”. Try a broader query/source or run bounded historical sync.`, data.count ? 'good' : 'warn'); renderSearchResults(data); }} catch (err) {{ STATE.searchResults = []; const p = err.payload || {{}}; setNotice('searchStatus', `${{esc(p.error || err.message)}}${{p.hint ? '<br/>' + esc(p.hint) : ''}}`, 'bad'); renderSearchResults(p); }} }}
function renderSearchResults(data={{}}) {{ if (STATE.searchResults.length) {{ qs('searchResults').innerHTML = STATE.searchResults.map(messageCard).join(''); return; }} const reason = data.empty_reason || data.error_code || 'no_matches'; const copy = reason === 'missing_query' ? 'Enter a query above to search stored local messages.' : reason === 'db_unavailable' ? 'The configured SQLite DB is unavailable. Check the DB path and runtime monitor.' : 'No matches in the current local DB/filter set. Try removing filters or loading older history.'; qs('searchResults').innerHTML = `<div class="empty">${{esc(copy)}}</div>`; }}
function searchMarkdown() {{ return (STATE.searchResults || []).map(m => `- \\`${{m.date}}\\` \\`${{m.dialog_id}}/${{m.msg_id}}\\` **${{m.sender || 'unknown'}}**: ${{(m.text || '').replace(/\\n/g,' ')}}`).join('\\n') + '\\n'; }}
function copySearch(format) {{ const body = format === 'markdown' ? searchMarkdown() : JSON.stringify({{messages: STATE.searchResults}}, null, 2); navigator.clipboard?.writeText(body); }}
function openThread(dialog, msg) {{ qs('threadDialog').value = dialog; qs('threadMessage').value = msg; showLane('thread'); loadThread(); }}
async function loadThread() {{ const data = await getJson('/api/thread?' + params({{...accountParam(), dialog: qs('threadDialog').value, message_id: qs('threadMessage').value, context: qs('threadContext').value, no_text: qs('metadataOnly').checked ? '1' : ''}})); STATE.thread = data; renderThread(data); }}
function renderThread(t) {{ qs('threadPreview').textContent = JSON.stringify(t, null, 2); const sum = t.export_summary || {{}}; qs('exportSummary').innerHTML = `<div class="meta"><span>source ${{esc(sum.source || t.source)}}</span><span>context ${{esc(sum.context_count ?? '—')}}</span><span>export ${{esc(sum.exported_at || '—')}}</span><span>metadata only ${{esc(sum.metadata_only ?? false)}}</span></div>`; const sections = [['Anchor message',[t.anchor]], ['Parent / reply chain', t.parents || []], ['Direct replies', t.replies || []], ['Nearby context', t.context || []]]; qs('threadBlocks').innerHTML = sections.map(([label, items]) => `<div class="thread-block"><h3>${{label}}</h3>${{items.length ? items.map(messageCard).join('') : '<div class="empty">None</div>'}}</div>`).join(''); }}
function downloadThread(format) {{ location.href = '/api/export/thread?' + params({{...accountParam(), dialog: qs('threadDialog').value, message_id: qs('threadMessage').value, context: qs('threadContext').value, format, no_text: qs('metadataOnly').checked ? '1' : ''}}); }}
function copyLocalRef() {{ navigator.clipboard?.writeText(`tg-monitor://dialog/${{qs('threadDialog').value}}/message/${{qs('threadMessage').value}}`); }}
function copyStatusJson() {{ navigator.clipboard?.writeText(JSON.stringify(STATE.dashboard || STATE.status || {{}}, null, 2)); }}
function exportRecentContext() {{ showLane('search'); qs('searchQuery').value = ''; qs('searchLimit').value = 100; getJson('/api/recent?' + params({{...accountParam(), limit:100}})).then(data => {{ STATE.searchResults = data.messages || []; qs('searchResults').innerHTML = STATE.searchResults.map(messageCard).join(''); copySearch('markdown'); }}); }}
const initial = new URLSearchParams(location.search); if (initial.get('dialog')) {{ qs('searchDialog').value = initial.get('dialog'); qs('threadDialog').value = initial.get('dialog'); }} if (initial.get('q')) qs('searchQuery').value = initial.get('q'); if (initial.get('thread_message')) qs('threadMessage').value = initial.get('thread_message');
refreshAll().then(() => {{ if (initial.get('q')) {{ showLane('search'); runSearch(); }} if (initial.get('thread_message')) {{ showLane('thread'); loadThread(); }} }}).catch(err => {{ qs('homeMetrics').innerHTML = metric('Workspace error', err.message, 'check DB path/API'); }});
</script>
</body></html>"""


def create_app(api_url: str = DEFAULT_API_URL, db_path: str | Path | None = None) -> LocalUiApp:
    app = LocalUiApp(api_url=api_url, db_path=resolve_db_path(str(db_path) if db_path else None))

    def index(_query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        return 200, "text/html; charset=utf-8", render_index_html(app.api_url, app.db_path), {}

    def api_status(_query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        return _json(_status_payload(app))

    def api_dashboard(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        status_payload = _status_payload(app)
        try:
            store_summary = build_store_summary(app.db_path, account_id=_account_id(query))
            store_error = None
        except DBUnavailable as exc:
            store_summary = {}
            store_error = str(exc)
        watched_sources = _watched_sources(status_payload, store_summary)
        payload = {
            "ok": True,
            "api_url": app.api_url,
            "db_path": app.db_path,
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "api_status": status_payload,
            "store": store_summary,
            "store_error": store_error,
            "watched_sources": watched_sources,
            "accounts": _accounts_payload(query),
            "read_only": True,
        }
        return _json(payload)

    def api_accounts(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        return _json(_accounts_payload(query))

    def api_dialogs(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        scope = query.get("scope") or "all"
        dialog_type = query.get("type") or (scope if scope in {"group", "channel", "dm"} else None)
        try:
            dialogs = _store(app, query).list_dialogs(
                dialog_type=dialog_type,
                query=query.get("query") or None,
                min_count=int(query.get("min_count", "0") or 0),
                limit=clamp_limit(query.get("limit"), default=300),
            )
        except ValueError as exc:
            return _error("bad_request", str(exc), status=400, hint="Adjust numeric filters and retry.")
        except DBUnavailable as exc:
            return _error("db_unavailable", str(exc), status=503, hint="Check --db/TG_MONITOR_DB and that monitor.db exists locally.")
        if scope == "watched":
            watched_ids = {str(source.get("id") or source.get("dialog_id")) for source in _watched_sources(_status_payload(app), {"sources": dialogs})}
            dialogs = [dialog for dialog in dialogs if str(dialog["id"]) in watched_ids]
        sort = query.get("sort") or "recent"
        if sort == "count":
            dialogs.sort(key=lambda item: int(item.get("message_count") or 0), reverse=True)
        elif sort == "stale":
            dialogs.sort(key=lambda item: item.get("latest_date") or "")
        else:
            dialogs.sort(key=lambda item: item.get("latest_date") or "", reverse=True)
        return _json({"ok": True, "source": "sqlite-readonly", "count": len(dialogs), "dialogs": dialogs})

    def api_recent(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        try:
            before_id = int(query["before_id"]) if query.get("before_id") else None
            messages = _store(app, query).recent_messages(
                minutes=int(query.get("minutes", "100000000") or 100000000) if not (before_id or query.get("before_date")) else None,
                dialog_id=query.get("dialog") or None,
                dialog_type=query.get("type") or None,
                sender=query.get("sender") or None,
                limit=clamp_limit(query.get("limit"), default=25),
                no_text=_truthy(query.get("no_text")),
                before_id=before_id,
                before_date=query.get("before_date") or None,
            )
        except ValueError as exc:
            return _error("bad_request", str(exc), status=400, hint="Use positive numeric limits/message ids and ISO dates.")
        except DBUnavailable as exc:
            return _error("db_unavailable", str(exc), status=503, hint="Check --db/TG_MONITOR_DB and that monitor.db exists locally.")
        return _json({"ok": True, "source": "sqlite-readonly", "count": len(messages), "messages": messages, "older_cursor": {"before_id": before_id, "before_date": query.get("before_date") or None}, "limit": clamp_limit(query.get("limit"), default=25)})

    def api_search(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        q = (query.get("q") or "").strip()
        if not q:
            return _error("missing_query", "Search query is required.", status=400, hint="Enter a keyword or phrase, then search the local SQLite DB.", extra={"empty_reason": "missing_query", "messages": []})
        try:
            since = query.get("since") or None
            if not since and query.get("minutes"):
                minutes = int(query.get("minutes", "0") or 0)
                if minutes < 1:
                    raise ValueError("minutes must be >= 1")
                since = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
            messages = _store(app, query).search_messages(
                q,
                dialog_id=query.get("dialog") or None,
                dialog_type=query.get("type") or None,
                sender=query.get("sender") or None,
                since=since,
                until=query.get("until") or None,
                limit=clamp_limit(query.get("limit"), default=25),
                no_text=_truthy(query.get("no_text")),
            )
        except ValueError as exc:
            return _error("bad_request", str(exc), status=400, hint="Use positive numeric filters and retry.")
        except DBUnavailable as exc:
            return _error("db_unavailable", str(exc), status=503, hint="Check --db/TG_MONITOR_DB and that monitor.db exists locally.")
        return _json({"ok": True, "source": "sqlite-readonly", "query": q, "count": len(messages), "messages": messages, "empty_reason": None if messages else "no_matches"})

    def api_thread(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        thread = _load_thread(app.db_path, query, account_id=_account_id(query))
        return _json({"ok": True, **thread})

    def api_export_thread(query: dict[str, str]) -> tuple[int, str, str, dict[str, str]]:
        thread = _load_thread(app.db_path, query, account_id=_account_id(query))
        if query.get("format") == "json":
            return 200, "application/json", json.dumps(thread, ensure_ascii=False, indent=2) + "\n", {"Content-Disposition": "attachment; filename=tg-thread.json"}
        return 200, "text/markdown; charset=utf-8", thread_to_markdown(thread), {"Content-Disposition": "attachment; filename=tg-thread.md"}

    app.router.add_get("/", index)
    app.router.add_get("/api/status", api_status)
    app.router.add_get("/api/dashboard", api_dashboard)
    app.router.add_get("/api/accounts", api_accounts)
    app.router.add_get("/api/dialogs", api_dialogs)
    app.router.add_get("/api/recent", api_recent)
    app.router.add_get("/api/search", api_search)
    app.router.add_get("/api/thread", api_thread)
    app.router.add_get("/api/export/thread", api_export_thread)
    return app


def build_store_summary(db_path: str | Path, account_id: str | None = None) -> dict[str, Any]:
    """Return home-dashboard facts from the local SQLite DB only."""
    store = ReadOnlyStore(db_path, account_id=account_id)
    with store.connect() as conn:
        has_account_id = store._has_account_id(conn)
        account_sql, account_params = store._account_where(has_account_id)
        where_sql = f" WHERE {account_sql}" if account_sql else ""
        total_messages = int(conn.execute(f"SELECT COUNT(*) FROM messages{where_sql}", account_params).fetchone()[0])
        type_rows = conn.execute(f"SELECT dialog_type, COUNT(*) FROM messages{where_sql} GROUP BY dialog_type", account_params).fetchall()
        latest_message_at = conn.execute(f"SELECT MAX(date) FROM messages{where_sql}", account_params).fetchone()[0]
    sources = store.list_dialogs(limit=1000)
    recent_activity = store.recent_messages(minutes=100000000, limit=8)
    return {
        "total_messages": total_messages,
        "by_type": {str(row[0] or "unknown"): int(row[1]) for row in type_rows},
        "latest_message_at": latest_message_at,
        "freshness": _freshness(latest_message_at),
        "source_count": len(sources),
        "sources": sources,
        "recent_activity": recent_activity,
    }


def _status_payload(app: LocalUiApp) -> dict[str, Any]:
    try:
        payload = ApiClient(app.api_url).status()
        return {"ok": True, "api_url": app.api_url, "db_path": app.db_path, "checked_at": datetime.now(timezone.utc).isoformat(), **payload}
    except ApiUnavailable as exc:
        return {"ok": False, "api_url": app.api_url, "db_path": app.db_path, "checked_at": datetime.now(timezone.utc).isoformat(), "telegram_status": "unavailable", "error": str(exc)}


def _watched_sources(status_payload: dict[str, Any], store_summary: dict[str, Any]) -> list[dict[str, Any]]:
    raw = status_payload.get("source_watchlist") or status_payload.get("watched_sources") or []
    if isinstance(raw, dict):
        if isinstance(raw.get("sources"), list):
            raw = raw["sources"]
        else:
            raw = [value for value in raw.values() if isinstance(value, dict)]
    watched: list[dict[str, Any]] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict):
            watched.append({"id": str(item.get("id") or item.get("dialog_id") or ""), "name": item.get("name") or item.get("dialog_name"), "type": item.get("type") or item.get("dialog_type"), "aliases": item.get("aliases", [])})
        else:
            watched.append({"id": str(item), "name": str(item), "type": "source"})
    if watched:
        return watched
    # Backward-compatible monitor behavior: without a configured watchlist, non-DM
    # dialogs are effectively the local source set operators care about.
    return [source for source in store_summary.get("sources", []) if source.get("type") != "dm"][:20]


def _account_id(query: dict[str, str]) -> str | None:
    account = (query.get("account") or "").strip()
    return account or None


def _store(app: LocalUiApp, query: dict[str, str]) -> ReadOnlyStore:
    return ReadOnlyStore(app.db_path, account_id=_account_id(query))


def _accounts_payload(query: dict[str, str]) -> dict[str, Any]:
    requested = _account_id(query)
    try:
        accounts = AccountRegistry().list_accounts()
    except Exception:
        accounts = []
    if not accounts:
        safe_accounts = [{"id": "default", "label": "Default account", "active": True}]
        active = "default"
        mode = "single"
    else:
        active = requested or next((account.id for account in accounts if account.active), accounts[0].id)
        safe_accounts = [
            {"id": account.id, "label": account.label, "active": account.id == active}
            for account in accounts
        ]
        mode = "multi" if len(safe_accounts) > 1 else "single"
    return {
        "ok": True,
        "mode": mode,
        "active_account": active,
        "accounts": safe_accounts,
        "read_only": True,
        "secrets_rendered": False,
        "session_paths_rendered": False,
    }


def _error(error_code: str, error: str, *, status: int, hint: str, extra: dict[str, Any] | None = None) -> tuple[int, str, str, dict[str, str]]:
    payload = {"ok": False, "error_code": error_code, "error": error, "hint": hint}
    if extra:
        payload.update(extra)
    return _json(payload, status=status)


def _load_thread(db_path: str | Path, query: dict[str, str], account_id: str | None = None) -> dict[str, Any]:
    thread = ReadOnlyStore(db_path, account_id=account_id).get_thread(query["dialog"], int(query["message_id"]), context=int(query.get("context", "10") or 10), max_depth=int(query.get("max_depth", "20") or 20))
    metadata_only = _truthy(query.get("no_text")) or _truthy(query.get("metadata_only"))
    if metadata_only:
        _redact_thread(thread)
    thread["export_summary"] = {
        "source": thread.get("source", "sqlite-readonly"),
        "dialog_id": thread["dialog_id"],
        "message_id": thread["message_id"],
        "context_count": len(thread.get("context", [])),
        "parent_count": len(thread.get("parents", [])),
        "reply_count": len(thread.get("replies", [])),
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "metadata_only": metadata_only,
    }
    return thread


def _redact_thread(thread: dict[str, Any]) -> None:
    for key in ("anchor",):
        if isinstance(thread.get(key), dict):
            thread[key]["text"] = None
    for key in ("parents", "replies", "context"):
        for msg in thread.get(key, []):
            msg["text"] = None


def _freshness(latest_message_at: str | None) -> dict[str, Any]:
    if not latest_message_at:
        return {"state": "unknown", "latest_age_minutes": None, "stale_after_minutes": STALE_AFTER_MINUTES}
    try:
        parsed = datetime.fromisoformat(str(latest_message_at).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        age_minutes = max(0, int((datetime.now(timezone.utc) - parsed.astimezone(timezone.utc)).total_seconds() // 60))
        return {"state": "fresh" if age_minutes <= STALE_AFTER_MINUTES else "stale", "latest_age_minutes": age_minutes, "stale_after_minutes": STALE_AFTER_MINUTES}
    except ValueError:
        return {"state": "unknown", "latest_age_minutes": None, "stale_after_minutes": STALE_AFTER_MINUTES}


def _truthy(value: str | None) -> bool:
    return str(value or "").lower() in {"1", "true", "yes", "on"}


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
        print(f"Read-only Telegram Sync workspace: http://{host}:{port}")
        server.serve_forever()
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Start the read-only Telegram Sync local workspace UI")
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--db", default=None)
    parser.add_argument("--host", default=DEFAULT_UI_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_UI_PORT)
    parser.add_argument("--allow-internal-bind", action="store_true", help="Explicitly allow binding non-localhost hosts for internal/Tailscale read-only review")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.host not in {"127.0.0.1", "localhost"} and not args.allow_internal_bind:
        print("Refusing non-localhost bind without --allow-internal-bind. This keeps the read-only workspace localhost-safe by default.", file=sys.stderr)
        return 2
    serve(create_app(args.api_url, args.db), args.host, args.port)
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
