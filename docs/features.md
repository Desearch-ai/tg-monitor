# Features

## Telegram ingestion

- Stores group/channel/DM messages in SQLite.
- Performs an initial 24-hour backfill per dialog, then unseen-only scans.
- Tracks `last_seen` per dialog to avoid duplicate inserts.
- Uses cancellation-safe Telethon list fetches in the canonical monitor loop.

## Source watchlist

- Runtime config can specify multiple Telegram groups/channels through `monitor_rules.json` or `TG_WATCH_SOURCES`/`TG_WATCH_GROUPS`.
- When configured, ingestion scans only matching sources; when omitted, existing all-dialog ingestion remains unchanged.
- `/status` surfaces the active watchlist mode and configured source count additively.

## Keyword/context lead candidates

- `signal_leads.py` matches configured keyword rules against stored messages and includes surrounding same-source context.
- `GET /lead-candidates` and `export_lead_candidates.py` emit `lead-candidates/v1` JSON with source, message reference, author, matched keywords, context excerpt, reason/confidence, suggested product/service, and approval status.
- Export is approval-safe: no Growth App insert, no Telegram send, and no user contact.

## Health/status API

- `GET /status`, `GET /health`, and `GET /` return a shared localhost JSON payload.
- Existing fields remain compatible; `telegram_ready` and optional diagnostics are additive.
- API binds to `127.0.0.1` and is suitable for local watchdog and smoke checks.

## TG Radar

- `tg_hot_topics_context.py` exports recent SQLite rows, topic keywords, reply-thread stats, and recent reply anchors.
- `tg_radar_context_compact.sh` trims message text before LLM analysis and can read a non-repo DB via `TG_MONITOR_DB`/`DB_PATH`.
- `tg_radar_report.sh` builds the report prompt and runs local OpenClaw inference.
- No radar script performs Telegram sends or live Telegram API calls.
