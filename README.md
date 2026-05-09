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
| `GET /status` | Backward-compatible service status with additive fields: `telegram_ready`, `total_messages`, `by_type`, optional `error`/`db_error`. |
| `GET /health` | Same payload as `/status`, for watchdogs and health probes. |
| `GET /` | Same payload as `/status`, useful for quick browser/curl checks. |
| `GET /messages?minutes=60&dialog=<id>&type=group&limit=200` | Recent stored messages from local SQLite. |
| `GET /dialogs` | Dialogs seen in the local DB. |
| `GET /groups` | Groups/channels seen in the local DB. |
| `POST /send` | Sends a Telegram message through the user session. Localhost-only and **manual approval gated**: do not wire this endpoint to unattended cron/agent workflows. |

Smoke test:

```bash
curl -fsS http://127.0.0.1:8765/status
```

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
uv run python -m unittest discover -s tests -v
```

The first command must print nothing; the second must print all four radar workflow files. Runtime copies in an ignored worktree should be removed from review worktrees, not committed.

## Safety notes

- Never commit `.env`, Telegram session files, `monitor.db*`, logs, snapshots, or generated radar reports.
- Do not call `POST /send` without explicit operator approval for the exact message, target, and reply anchor.
- Prefer read-only validation (`GET /status`, SQLite queries, radar dry-runs) before any PM2 cutover.

## O-71 read-only Telegram Sync CLI + local UI

This repo includes a read-first operator surface inspired by local sync CLIs: `tg_sync` exposes a scriptable CLI and a localhost-only browser UI for inspecting Telegram Monitor health, dialogs, messages, search results, and thread exports without touching Telegram write paths.

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

# Recent/search messages. Use --no-text for safe evidence captures.
uv run python -m tg_sync.cli messages --minutes 60 --limit 20 --no-text
uv run python -m tg_sync.cli recent --dialog -1002564889965 --limit 20 --json --no-text
uv run python -m tg_sync.cli search "bittensor" --type group --limit 25 --json --no-text

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

The UI binds to `127.0.0.1` by default and refuses non-localhost hosts. It shows service/session health, a dialog list, search/recent messages, thread view/export, and a visible read-only banner. Compose/reply/delete controls are disabled placeholders only.

Safety boundary: O-71 does **not** add `send`, `reply`, or `delete` CLI commands. The UI does not register `/send` or `/api/send` routes and does not call the existing `POST /send` endpoint. Telegram sends/replies/deletes remain manual/operator-approved only; do not connect these tools to autonomous agent or cron write workflows.
