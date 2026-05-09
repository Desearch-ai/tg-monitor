# Architecture

## Components

- `monitor.py` — Telethon ingestion loop, SQLite persistence, and localhost aiohttp API.
- `monitor.db` — local runtime SQLite database, intentionally ignored and not committed.
- `ecosystem.config.js` — PM2 process definition for the canonical repo runtime after cutover.
- `tg_hot_topics_context.py` — read-only SQLite exporter for recent hot-topic context.
- `signal_leads.py` / `export_lead_candidates.py` — read-only keyword/context matching and lead-candidate artifact export.
- `monitor_rules.example.json` — copyable source watchlist + keyword/context rules template; runtime `monitor_rules.json` stays ignored.
- `tg_radar_context_compact.sh` — trims exporter JSON into compact LLM input.
- `tg_radar_report.sh` / `hot_topics_cron_prompt.md` — report-generation wrapper and prompt asset.

## Runtime model

The monitor starts the HTTP API before Telegram initialization, so watchdogs can receive HTTP 200 during startup. `/status`, `/health`, and `/` share the same additive JSON payload:

```json
{
  "status": "starting|running",
  "telegram_ready": false,
  "total_messages": 0,
  "by_type": {},
  "source_watchlist": {"mode": "all_non_dm_dialogs", "count": 0, "sources": []}
}
```

Once Telegram connects and dialogs are loaded, `telegram_ready` becomes `true` and `status` becomes `running`.

## Artifact boundary

Source-controlled files are code, docs, and config only. Runtime state stays local:

- secrets/env: `.env*`
- Telegram credentials/sessions: `user_session.session*`, `*.session`, `*.session-journal`
- data/logs/generated snapshots: `monitor.db*`, `monitor.log`, `snapshot_*.json`, `health.json`, radar JSON/MD outputs
- auth helpers: `reauth.exp` and local-only reauth/session backups

## Lead-candidate boundary

`GET /lead-candidates` and `export_lead_candidates.py` are read-only. They emit `lead-candidates/v1` JSON for human review/import tooling and include `approval_status` so candidates can remain gated before any outreach. They do not call Telegram APIs, Growth App APIs, or `/send`.

## Send boundary

`POST /send` is available only on `127.0.0.1` and must remain human/operator approved. Radar, health, and lead-candidate workflows are read-only and must not call `/send`.
