# Features

## Telegram ingestion

- Stores group/channel/DM messages in SQLite.
- Performs an initial 24-hour backfill per dialog, then unseen-only scans.
- Tracks `last_seen` per dialog to avoid duplicate inserts.
- Uses cancellation-safe Telethon list fetches in the canonical monitor loop.

## Health/status API

- `GET /status`, `GET /health`, and `GET /` return a shared localhost JSON payload.
- Existing fields remain compatible; `telegram_ready` and optional diagnostics are additive.
- API binds to `127.0.0.1` and is suitable for local watchdog and smoke checks.

## TG Radar

- `tg_hot_topics_context.py` exports recent SQLite rows, topic keywords, reply-thread stats, and recent reply anchors.
- `tg_radar_context_compact.sh` trims message text before LLM analysis.
- `tg_radar_report.sh` builds the report prompt and runs local OpenClaw inference.
- No radar script performs Telegram sends or live Telegram API calls.
