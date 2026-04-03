#!/usr/bin/env bash
# TG Monitor — Manual Health Check
# Run this to quickly diagnose the state of both ingestion and scheduler layers.
# Usage: bash health-check.sh

set -euo pipefail

OPENCLAW="/Users/giga/.nvm/versions/node/v22.22.0/bin/openclaw"
SNAPSHOT="/Users/giga/projects/openclaw/tg-monitor/snapshot_nerds.json"
SUMMARY_CRON_ID="4f231cdf-01f9-407c-bf44-3eb2d66661a9"
WATCHDOG_CRON_ID="66e73acb-fc8f-424e-ae09-6b360ae6402a"
RED='\033[0;31m'; YELLOW='\033[1;33m'; GREEN='\033[0;32m'; NC='\033[0m'

echo "=== TG Monitor Health Check ==="
echo ""

# --- Ingestion layer ---
echo "📥 INGESTION LAYER"

if [ -f "$SNAPSHOT" ]; then
  SNAPSHOT_AGE_H=$(python3 -c "import os,time; print(f'{(time.time()-os.path.getmtime(\"$SNAPSHOT\"))/3600:.1f}')")
  SNAPSHOT_COUNT=$(python3 -c "import json; d=json.load(open('$SNAPSHOT')); print(d.get('count',0))" 2>/dev/null || echo "?")
  if python3 -c "import os,time; exit(0 if (time.time()-os.path.getmtime('$SNAPSHOT'))/3600 < 6 else 1)"; then
    echo -e "  ${GREEN}✓${NC} snapshot_nerds.json — ${SNAPSHOT_AGE_H}h old, ${SNAPSHOT_COUNT} messages"
  else
    echo -e "  ${RED}✗${NC} snapshot_nerds.json — STALE: ${SNAPSHOT_AGE_H}h old (expected < 6h)"
    echo -e "    Fix: pm2 restart tg-monitor"
  fi
else
  echo -e "  ${RED}✗${NC} snapshot_nerds.json — MISSING"
fi

TG_API=$(curl -s --max-time 3 http://127.0.0.1:8765/status 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print('OK: '+str(d))" 2>/dev/null || echo "DOWN")
if [[ "$TG_API" == DOWN ]]; then
  echo -e "  ${RED}✗${NC} HTTP API :8765 — DOWN"
  echo -e "    Fix: pm2 restart tg-monitor"
else
  echo -e "  ${GREEN}✓${NC} HTTP API :8765 — $TG_API"
fi

PM2_STATUS=$(pm2 jlist 2>/dev/null | python3 -c "
import json,sys
procs=json.load(sys.stdin)
p=next((x for x in procs if x.get('name')=='tg-monitor'),None)
if p: print(p.get('pm2_env',{}).get('status','unknown'))
else: print('not found')
" 2>/dev/null || echo "unknown")
if [[ "$PM2_STATUS" == "online" ]]; then
  echo -e "  ${GREEN}✓${NC} pm2 tg-monitor — online"
else
  echo -e "  ${YELLOW}⚠${NC}  pm2 tg-monitor — ${PM2_STATUS}"
fi

echo ""

# --- Scheduler layer ---
echo "📡 SCHEDULER LAYER"

CRON_JSON=$($OPENCLAW cron list --json 2>/dev/null || echo '{"jobs":[]}')

# Summary cron
SUMMARY=$(echo "$CRON_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
jobs=data.get('jobs',[])
j=next((x for x in jobs if x['id']=='$SUMMARY_CRON_ID'),None)
if not j:
    print('MISSING')
else:
    enabled=j.get('enabled',False)
    errs=j.get('state',{}).get('consecutiveErrors',0)
    last=j.get('state',{}).get('lastRunStatus','unknown')
    next_run=j.get('state',{}).get('nextRunAtMs',0)
    import time
    next_min=max(0,int((next_run/1000-time.time())/60))
    print(f'{\"ENABLED\" if enabled else \"DISABLED\"} errs={errs} last={last} next={next_min}min')
")

if [[ "$SUMMARY" == "MISSING" ]]; then
  echo -e "  ${RED}✗${NC} Summary cron — MISSING from registry"
  echo -e "    Fix: openclaw cron enable $SUMMARY_CRON_ID"
elif [[ "$SUMMARY" == DISABLED* ]]; then
  echo -e "  ${RED}✗${NC} Summary cron — DISABLED ($SUMMARY)"
  echo -e "    Fix: $OPENCLAW cron enable $SUMMARY_CRON_ID"
elif echo "$SUMMARY" | grep -q "errs=[2-9]"; then
  echo -e "  ${YELLOW}⚠${NC}  Summary cron — $SUMMARY (repeated errors)"
  echo -e "    Fix: $OPENCLAW cron run $SUMMARY_CRON_ID"
else
  echo -e "  ${GREEN}✓${NC} Summary cron — $SUMMARY"
fi

# Watchdog cron
WATCHDOG=$(echo "$CRON_JSON" | python3 -c "
import json,sys
data=json.load(sys.stdin)
jobs=data.get('jobs',[])
j=next((x for x in jobs if x['id']=='$WATCHDOG_CRON_ID'),None)
if not j: print('MISSING')
else: print('ENABLED' if j.get('enabled') else 'DISABLED')
")

if [[ "$WATCHDOG" == "MISSING" ]]; then
  echo -e "  ${RED}✗${NC} Watchdog cron — MISSING"
elif [[ "$WATCHDOG" == "DISABLED" ]]; then
  echo -e "  ${YELLOW}⚠${NC}  Watchdog cron — DISABLED (not monitoring!)"
  echo -e "    Fix: $OPENCLAW cron enable $WATCHDOG_CRON_ID"
else
  echo -e "  ${GREEN}✓${NC} Watchdog cron — running every 30min"
fi

echo ""
echo "=== Done. See RUNBOOK.md for recovery instructions. ==="
