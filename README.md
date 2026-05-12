# Telegram Monitor

Telethon + SQLite monitor for Telegram groups/channels/DMs relevant to Desearch and the Bittensor ecosystem. It runs a localhost-only HTTP API on `127.0.0.1:8765` for health checks, recent-message inspection, and manual/operator-approved sends.

## Runtime boundary

- Canonical source repo: `/Users/giga/projects/openclaw/tg-monitor` (`Desearch-ai/tg-monitor`).
- Current live PM2 runtime, until the approved cutover: `/Users/giga/.openclaw/workspace/tg-monitor`.
- Runtime-only artifacts are intentionally not source-controlled: `.env*`, `monitor.db*`, `user_session.session*`, `monitor.log`, `snapshot_*.json`, `health.json`, `nohup.out`, and auth helper files.
- The checked-in `ecosystem.config.js` is prepared for running from the canonical repo path after the separate cutover task.

## Setup

```bash
cd /Users/giga/projects/openclaw/tg-monitor
uv pip install -r requirements.txt
cp .env.example .env
# Fill TG_API_ID, TG_API_HASH, TG_PHONE, and optional API_PORT.
uv run python monitor.py
```

PM2 runtime after cutover:

```bash
pm2 start ecosystem.config.js
pm2 status tg-monitor
```

## Local API

All endpoints bind to localhost only.

| Endpoint | Purpose |
| --- | --- |
| `GET /status` | Backward-compatible service status with additive fields: `telegram_ready`, `total_messages`, `by_type`, `source_watchlist`, optional `error`/`db_error`. |
| `GET /health` | Same payload as `/status`, for watchdogs and health probes. |
| `GET /` | Same payload as `/status`, useful for quick browser/curl checks. |
| `GET /messages?minutes=60&dialog=<id>&type=group&limit=200` | Recent stored messages from local SQLite. Sender id is additive when available. |
| `GET /lead-candidates?minutes=1440&limit=500` | Read-only keyword/context candidate export for #tg-alerts/Growth App review. No Growth App write and no Telegram contact. |
| `GET /dialogs` | Dialogs seen in the local DB. |
| `GET /groups` | Groups/channels seen in the local DB. |
| `POST /send` | Sends a Telegram message through the user session. Localhost-only and **manual approval gated**: do not wire this endpoint to unattended cron/agent workflows. |

Smoke test:

```bash
curl -fsS http://127.0.0.1:8765/status
```

## Source watchlist and lead-candidate export

Use `monitor_rules.example.json` as the template for a runtime-only `monitor_rules.json` (ignored by git), then set `TG_MONITOR_CONFIG=monitor_rules.json`. `source_watchlist` can include multiple groups/channels by Telegram dialog id, name, type, and aliases. If no watchlist is configured, ingestion remains backward-compatible and scans all dialogs; exports default to all non-DM dialogs.

Keyword rules define `keywords`, `reason`, `confidence`, and `suggested_product_service`. Matching produces review-safe candidates with source, message reference, author info where available, `rule_id`, matched keywords, context excerpt, surrounding messages, reason/confidence, suggested product/service, and `approval_status`.

```bash
cp monitor_rules.example.json monitor_rules.json
TG_MONITOR_CONFIG=monitor_rules.json TG_MONITOR_DB=/path/to/monitor.db \
  ./export_lead_candidates.py --minutes 1440 --output /tmp/tg-lead-candidates.json

curl -fsS 'http://127.0.0.1:8765/lead-candidates?minutes=1440&limit=500'
```

Schema: `docs/lead_candidates.schema.json`. This task only writes an artifact/schema for later review/import; it does **not** insert Growth App leads, call Growth App APIs, send Telegram messages, or contact users.

## TG Radar workflow

The radar scripts read the local SQLite DB and produce context for a Discord-facing hot-topic report. They do not call Telegram APIs and do not send Telegram messages.

```bash
# JSON context for the default Nerds group.
./tg_hot_topics_context.py --hours 4 --reply-recency-minutes 90 --output /tmp/tg-hot-topics-context.json

# Compact JSON for an LLM/reporting agent.
TG_MONITOR_DB=/Users/giga/.openclaw/workspace/tg-monitor/monitor.db ./tg_radar_context_compact.sh > /tmp/tg-hot-topics-compact.json

# Full report prompt + local OpenClaw inference wrapper.
./tg_radar_report.sh
```

Set `TG_MONITOR_DB` or `DB_PATH` when validating against the active runtime DB without copying it into the repo worktree.

`hot_topics_cron_prompt.md` is the prompt asset for the scheduled report. Keep generated JSON/Markdown outputs in `/tmp` or another ignored runtime path.

## Repository boundary verification

Before opening or reviewing a PR, confirm the branch contains source/docs/config only and that radar files are actually tracked:

```bash
git ls-files .env monitor.db monitor.log snapshot_nerds.json snapshot_state.json 'user_session.session*' '*.session' '*.session-journal'
git ls-files tg_hot_topics_context.py tg_radar_context_compact.sh tg_radar_report.sh hot_topics_cron_prompt.md
# Optional with a real runtime DB path:
uv run python ./export_lead_candidates.py --db /path/to/monitor.db --output /tmp/tg-lead-candidates.json
uv run python -m unittest discover -s tests -v
```

The first command must print nothing; the second must print all four radar workflow files. Runtime copies in an ignored worktree should be removed from review worktrees, not committed.

## Safety notes

- Never commit `.env`, Telegram session files, `monitor.db*`, logs, snapshots, runtime `monitor_rules.json`, generated radar reports, or lead-candidate artifacts.
- Do not call `POST /send` without explicit operator approval for the exact message, target, and reply anchor.
- Prefer read-only validation (`GET /status`, SQLite queries, radar dry-runs) before any PM2 cutover.

## O-71 read-only Telegram Sync CLI + local UI

This repo includes a read-first operator surface inspired by birdclaw/local sync workspaces: `tg_sync` exposes a scriptable CLI and a localhost-only browser app for inspecting Telegram Monitor health, sources, local DB search, message threads, and exports without touching Telegram write paths.

UI implementation tradeoff: the repo is Python-only (`requirements.txt`, no `package.json`), so O-71 is implemented as a Python-served HTML/CSS/JS app instead of adding React/Vite tooling. The browser surface is still structured as four app lanes, not a generated debug form.

Install/runtime notes:

```bash
cd /Users/giga/projects/openclaw/tg-monitor
uv pip install -r requirements.txt

# Use the live runtime DB explicitly while the PM2 runtime still lives outside the canonical repo.
export TG_MONITOR_DB=/Users/giga/.openclaw/workspace/tg-monitor/monitor.db
```

CLI examples:

```bash
# Service/session health via existing read-only localhost API.
uv run python -m tg_sync.cli status
uv run python -m tg_sync.cli health --json

# Dialogs/chats with SQLite enrichment when TG_MONITOR_DB/--db is available.
uv run python -m tg_sync.cli chats --limit 25
uv run python -m tg_sync.cli dialogs --type group --limit 50 --json
uv run python -m tg_sync.cli groups --json

# Account registry and bounded historical backfill. Registry JSON stores only metadata, not API secrets or session files.
uv run python -m tg_sync.cli accounts list --json
uv run python -m tg_sync.cli accounts add ops --session sessions/ops --db db/ops.db --json
uv run python -m tg_sync.cli accounts switch ops --json
uv run python -m tg_sync.cli accounts status --json
uv run python -m tg_sync.cli sync backfill --account ops --dialog -1002564889965 --limit 100 --before-id <older_than_msg_id> --dry-run --json

# Recent/search messages. Use --account to scope reads when multiple accounts share a DB. Use --no-text for safe evidence captures.
uv run python -m tg_sync.cli messages --account ops --minutes 60 --limit 20 --no-text
uv run python -m tg_sync.cli recent --dialog -1002564889965 --limit 20 --json --no-text
uv run python -m tg_sync.cli search "bittensor" --account ops --type group --limit 25 --json --no-text

# Thread view/export from local SQLite only.
uv run python -m tg_sync.cli thread --dialog -1002564889965 --message-id <msg_id> --context 10 --json --no-text
uv run python -m tg_sync.cli export thread --dialog -1002564889965 --message-id <msg_id> --format markdown --output /tmp/tg-thread.md --no-text
uv run python -m tg_sync.cli export messages --dialog -1002564889965 --format jsonl --output /tmp/tg-messages.jsonl --no-text

# Polling helpers; stop with Ctrl-C.
uv run python -m tg_sync.cli tail --dialog -1002564889965 --interval 5 --no-text
uv run python -m tg_sync.cli watch status --interval 5
```

Local UI:

```bash
uv run python -m tg_sync.ui \
  --api-url http://127.0.0.1:8765 \
  --db /Users/giga/.openclaw/workspace/tg-monitor/monitor.db \
  --host 127.0.0.1 \
  --port 8787
# Open http://127.0.0.1:8787
```

The UI binds to `127.0.0.1` by default and refuses non-localhost hosts. Open `http://127.0.0.1:8787` after starting it.

Workspace lanes:

- **Home / Sync Dashboard** — API status, Telegram readiness, DB path, total local messages, by-type counts, source/watchlist summary, latest message freshness, recent activity, read-only state, and primary actions for Search, Chats, recent-context export, and JSON status copy.
- **Chats / Sources** — left-side source list with all/watched/groups/channels/DM filters, name/id search, recent/count/stale sorting, selected-chat header, local message list, reply indicators, and Open thread actions.
- **Search / Research** — local SQLite search with query, source, type, sender, date/recency, and limit filters. Results show source, sender, timestamp, preview context, Open thread, and JSON/Markdown copy helpers. This is local DB search, not a live Telegram query.
- **Thread / Export** — anchor message, parent chain, direct replies, nearby context, Markdown/JSON download controls, copyable local refs, metadata-only redaction toggle, and export summary with context counts/source/export timestamp.

Safety boundary: O-71 does **not** add `send`, `reply`, or `delete` CLI commands. Historical sync is an explicit operator action and is capped (`--limit`, max 1000) with `--dry-run` support; it only reads Telegram messages and writes local SQLite rows. The UI does not register `/send` or `/api/send` routes, does not call the existing Telegram write endpoint, and intentionally shows no compose/reply/delete placeholders. Telegram sends/replies/deletes remain manual/operator-approved only; do not connect these tools to autonomous agent or cron write workflows.
