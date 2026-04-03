# TG Monitor — Ops Runbook

Last updated: 2026-04-03
Related task: `5ee1f3cb` — Protect tg-monitor summary cron from silent disablement

---

## Overview

The tg-monitor stack has **two independent layers**. Failures in one don't always cause failures in the other.

| Layer | What it does | Key files |
|-------|-------------|-----------|
| **Ingestion** | Telethon monitors Telegram group, writes to SQLite + snapshot | `monitor.py`, `monitor.db`, `snapshot_nerds.json` |
| **Scheduler** | OpenClaw cron reads snapshot every 4h, posts summary to Discord | Cron ID `4f231cdf-01f9-407c-bf44-3eb2d66661a9` |

The watchdog cron (`66e73acb-fc8f-424e-ae09-6b360ae6402a`) runs every 30 minutes and distinguishes between these two failure modes.

---

## Failure Scenarios

### Scenario A: Summary cron is disabled

**Symptoms:**
- No summaries posted to `#tg-alerts` for 4+ hours
- `snapshot_nerds.json` is fresh (updated recently)
- Watchdog posts: `⚠️ TG Watchdog: Nerds Summary cron was DISABLED — auto-re-enabled.`

**Why it happens:**
- Cron was manually disabled in OpenClaw UI or via CLI
- Gateway restart in some edge cases can affect cron state

**Automatic recovery:** The watchdog auto-re-enables it. Summaries resume on next 4h tick.

**Manual recovery (one step):**
```bash
openclaw cron enable 4f231cdf-01f9-407c-bf44-3eb2d66661a9
```

**Verify fix:**
```bash
openclaw cron list | grep "TG Monitor"
# Should show: enabled: true
```

---

### Scenario B: Summary cron is missing from registry

**Symptoms:**
- No summaries posted
- `openclaw cron list --json` does not contain ID `4f231cdf-01f9-407c-bf44-3eb2d66661a9`
- Watchdog posts: `🚨 TG Watchdog: Summary cron is MISSING from registry!`

**Why it happens:**
- Cron was deleted manually
- Corrupted gateway state after a crash

**Recovery — recreate the cron:**
```bash
# Run this from the openclaw workspace directory
openclaw cron add \
  --name "TG Monitor — Nerds Group Summary" \
  --every 14400000 \
  --session-target isolated \
  --model "anthropic/claude-haiku-4" \
  --timeout 300 \
  --delivery announce:discord:channel:1476995202177826888
```

Or use the OpenClaw API / UI to recreate with the payload from `cron-backup.json` in this repo.

---

### Scenario C: Summary cron has repeated timeout errors

**Symptoms:**
- Cron is enabled but `consecutiveErrors >= 2`
- `snapshot_nerds.json` is fresh
- Watchdog posts: `⚠️ TG Watchdog: Nerds Summary cron has N consecutive failures.`

**Why it happens:**
- Model too slow (old: `gpt-5.4` with 150s timeout — now fixed)
- Snapshot file too large for context window
- Transient network/API issue

**Recovery:**
```bash
# Run the cron immediately to test
openclaw cron run 4f231cdf-01f9-407c-bf44-3eb2d66661a9

# Check run history
openclaw cron runs 4f231cdf-01f9-407c-bf44-3eb2d66661a9
```

If it keeps timing out, check the model and timeout in the cron config:
- Model: `anthropic/claude-haiku-4` (fast, reliable)
- Timeout: `300s` (5 minutes — sufficient for file read + summary)

---

### Scenario D: Snapshot is stale (ingestion failure)

**Symptoms:**
- `snapshot_nerds.json` is older than 6 hours
- Cron may be healthy but has nothing fresh to summarize
- Watchdog posts: `🔴 TG Watchdog: Snapshot is stale (Xh old).`

**Why it happens:**
- `tg-monitor` pm2 process crashed or disconnected from Telegram
- Telethon session expired (requires re-auth)
- Network issue on the host machine

**Diagnosis:**
```bash
# Check pm2 status
pm2 status tg-monitor

# Check the HTTP API
curl http://127.0.0.1:8765/status

# Check recent logs
pm2 logs tg-monitor --lines 50 --nostream

# Check the health file
cat /Users/giga/projects/openclaw/tg-monitor/health.json
```

**Recovery:**
```bash
# Step 1: Restart tg-monitor
pm2 restart tg-monitor

# Step 2: Watch logs for reconnection
pm2 logs tg-monitor --lines 20

# Step 3: Verify snapshot updates within ~5 minutes
watch -n 30 "ls -la /Users/giga/projects/openclaw/tg-monitor/snapshot_nerds.json"
```

If Telethon session is expired (you'll see "FloodWaitError" or auth errors in logs):
```bash
cd /Users/giga/projects/openclaw/tg-monitor
uv run python monitor.py  # Re-authenticate interactively
```

---

### Scenario E: Dual failure (ingestion + scheduler both broken)

**Symptoms:**
- `snapshot_nerds.json` stale AND cron has errors
- Watchdog posts: `🔴 TG Watchdog: DUAL FAILURE — cron erroring AND snapshot stale.`

**Recovery:**
1. Fix ingestion first (Scenario D)
2. Then fix scheduler (Scenario A or C)

---

## Quick Reference — One-Liners

| Problem | Fix |
|---------|-----|
| Summary cron disabled | `openclaw cron enable 4f231cdf-01f9-407c-bf44-3eb2d66661a9` |
| Run summary now | `openclaw cron run 4f231cdf-01f9-407c-bf44-3eb2d66661a9` |
| Check cron state | `openclaw cron list --json \| python3 -c "import json,sys; jobs=json.load(sys.stdin)['jobs']; j=next((x for x in jobs if x['id']=='4f231cdf-01f9-407c-bf44-3eb2d66661a9'),None); print(j['enabled'] if j else 'MISSING')"` |
| Check snapshot age | `python3 -c "import os,time; p='snapshot_nerds.json'; print(f'{(time.time()-os.path.getmtime(p))/3600:.1f}h old')"` |
| Restart tg-monitor | `pm2 restart tg-monitor` |
| View tg-monitor health | `curl http://127.0.0.1:8765/status` |
| Check watchdog | `openclaw cron list \| grep Watchdog` |

---

## Cron Registry (Telegram Monitor jobs)

| Cron ID | Name | Schedule | Status |
|---------|------|----------|--------|
| `4f231cdf-01f9-407c-bf44-3eb2d66661a9` | TG Monitor — Nerds Group Summary | Every 4h | Active |
| `66e73acb-fc8f-424e-ae09-6b360ae6402a` | TG Watchdog — Summary Cron Health Check | Every 30min | Active |

---

## Watchdog Behavior

The watchdog (`66e73acb`) runs every 30 minutes and:
1. Checks if cron `4f231cdf` exists in the registry
2. Checks if it's enabled
3. Checks `consecutiveErrors` — alerts if ≥ 2
4. Checks `snapshot_nerds.json` age — alerts if > 6 hours

**Alert routing:** All alerts go to `#tg-alerts` (Discord `1476995202177826888`).

**Silence policy:** If everything is healthy, the watchdog replies `WATCHDOG_OK` and posts nothing to Discord.

**Auto-healing:** If the cron is found disabled, the watchdog **automatically re-enables it** before alerting.

---

## Files in This Repo

| File | Purpose |
|------|---------|
| `monitor.py` | Main Telethon monitor — ingests Telegram messages |
| `snapshot_nerds.json` | Latest ~4h window of messages from τhe nerds group |
| `snapshot_state.json` | Cursor/state for the snapshot writer |
| `health.json` | Last health check from the HTTP API |
| `monitor.db` | SQLite database of all ingested messages |
| `ecosystem.config.js` | pm2 config — starts `tg-monitor` service |
| `RUNBOOK.md` | This file |
