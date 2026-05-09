# Telegram Monitor

Telethon + SQLite monitor for Telegram groups/channels/DMs relevant to Desearch and the Bittensor ecosystem. It runs a localhost-only HTTP API on `127.0.0.1:8765` for health checks, recent-message inspection, and manual/operator-approved sends.

## Runtime boundary

- Canonical source repo: `/Users/giga/projects/openclaw/tg-monitor` (`Desearch-ai/tg-monitor`).
- Current live PM2 runtime, until the approved cutover: `/Users/giga/.openclaw/workspace/tg-monitor`.
- Runtime-only artifacts are intentionally not source-controlled: `.env*`, `monitor.db`, `user_session.session*`, `monitor.log`, `snapshot_*.json`, `health.json`, `nohup.out`, and auth helper files.
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
./tg_radar_context_compact.sh > /tmp/tg-hot-topics-compact.json

# Full report prompt + local OpenClaw inference wrapper.
./tg_radar_report.sh
```

`hot_topics_cron_prompt.md` is the prompt asset for the scheduled report. Keep generated JSON/Markdown outputs in `/tmp` or another ignored runtime path.

## Safety notes

- Never commit `.env`, Telegram session files, `monitor.db`, logs, snapshots, or generated radar reports.
- Do not call `POST /send` without explicit operator approval for the exact message, target, and reply anchor.
- Prefer read-only validation (`GET /status`, SQLite queries, radar dry-runs) before any PM2 cutover.
