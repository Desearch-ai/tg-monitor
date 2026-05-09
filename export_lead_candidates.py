#!/usr/bin/env python3
"""Export Growth-App-safe Telegram lead candidates from local monitor.db.

Read-only: no Telegram sends, no Telegram API calls, no Growth App writes.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import signal_leads

DEFAULT_DB = Path(__file__).with_name("monitor.db")
DEFAULT_OUTPUT = "/tmp/tg-lead-candidates.json"


def main() -> None:
    ap = argparse.ArgumentParser(description="Export keyword/context lead candidates from tg-monitor SQLite")
    ap.add_argument("--db", default=os.environ.get("TG_MONITOR_DB") or os.environ.get("DB_PATH") or str(DEFAULT_DB))
    ap.add_argument("--config", default=os.environ.get("TG_MONITOR_CONFIG"), help="JSON rules config path")
    ap.add_argument("--minutes", type=int, default=None, help="Lookback window; defaults to config lead_export.default_minutes")
    ap.add_argument("--limit", type=int, default=None, help="Max messages to scan; defaults to config lead_export.default_limit")
    ap.add_argument("--include-dms", action="store_true", help="Include DMs in candidate export; off by default")
    ap.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path, or '-' for stdout")
    args = ap.parse_args()

    payload = signal_leads.export_lead_candidates(
        args.db,
        config_path=args.config,
        minutes=args.minutes,
        limit=args.limit,
        include_dms=args.include_dms,
    )

    if args.output == "-":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"wrote {payload['candidate_count']} candidates → {args.output}")


if __name__ == "__main__":
    main()
