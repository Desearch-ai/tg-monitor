# Known Issues / Operational Notes

- PM2 still runs the active runtime from `/Users/giga/.openclaw/workspace/tg-monitor` until a separate approved cutover moves it to the canonical repo path.
- The live `monitor.db` and Telegram session are runtime state. They must be copied/provisioned locally for a new runtime but never committed.
- `tg_radar_report.sh` depends on the local `openclaw` CLI and `jq`; if those are unavailable, run `tg_radar_context_compact.sh` and feed the JSON to the reporting agent manually.
- Generated radar context may contain private Telegram message excerpts. Keep generated JSON/Markdown in `/tmp` or another ignored path.
- `POST /send` is intentionally not used by any checked-in automation. External sends remain explicit-approval/manual gated.
