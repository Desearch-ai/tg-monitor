# Telegram Sync CLI Operator Guide

## Overview
The Telegram Sync CLI provides operators with tools to:
- Manage multiple Telegram monitoring accounts
- Search and analyze monitored messages
- Initiate targeted syncs of historical data
- Export message data for analysis
- Monitor real-time activity

## Quick Start

1. Install dependencies:
   ```bash
   uv pip install -r requirements.txt
   ```

2. Configure environment (copy from example):
   ```bash
   cp .env.example .env
   ```

3. Start the monitor service:
   ```bash
   pm2 start ecosystem.config.js
   ```

## Account Management

### Listing Accounts
```bash
uv run python -m tg_sync.cli accounts list
```

### Setting Active Account
```bash
uv run python -m tg_sync.cli accounts set <account-name>
```

## Search & Read Operations

### Basic Search
```bash
uv run python -m tg_sync.cli search "query terms" --limit 20
```

### View Recent Messages
```bash
uv run python -m tg_sync.cli recent --limit 50
```

## Sync Operations

### Targeted Sync
```bash
uv run python -m tg_sync.cli sync --groups=-1002564889965
```

### Bounded Sync for New Messages
```bash
uv run python -m tg_sync.cli watch
```

## Deployment Notes

For Tailscale access, use URLs in the format:
```
http://100.113.216.73:8765/
```

## Troubleshooting

- Check service status: `uv run python -m tg_sync.cli status`
- Verify health: `uv run python -m tg_sync.cli health`
- Reset DB: `rm monitor.db && touch monitor.db` (when safe)
