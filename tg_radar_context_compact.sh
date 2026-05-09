#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON_BIN="${PYTHON:-python3}"
RAW=/tmp/tg-hot-topics-context.json
./tg_hot_topics_context.py --hours 4 --reply-recency-minutes 90 --output "$RAW"
"$PYTHON_BIN" - <<'PY'
import json
from pathlib import Path
raw = json.loads(Path('/tmp/tg-hot-topics-context.json').read_text())

def trim(s, n=260):
    s = (s or '').replace('\n', ' ').strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + '…'

messages = []
for m in raw.get('messages', []):
    messages.append({
        'msg_id': m.get('msg_id'),
        'sender': m.get('sender'),
        'age_minutes': m.get('age_minutes'),
        'reply_to_id': m.get('reply_to_id'),
        'text': trim(m.get('text'), 220),
    })

anchors = []
for m in raw.get('reply_anchor_candidates', []):
    item = {
        'msg_id': m.get('msg_id'),
        'sender': m.get('sender'),
        'age_minutes': m.get('age_minutes'),
        'reply_to_id': m.get('reply_to_id'),
        'text': trim(m.get('text'), 520),
    }
    parent = m.get('reply_parent')
    if parent:
        item['reply_parent'] = {'sender': parent.get('sender'), 'text': trim(parent.get('text'), 320)}
    anchors.append(item)

# Engagement signals from reply threads and recent anchors.
reply_counts = {str(x.get('thread_root','')).split(':')[-1]: x.get('reply_count_in_window',0) for x in raw.get('stats',{}).get('reply_threads',[])}
engagement = []
by_msg = {str(m.get('msg_id')): m for m in messages}
for msg_id, count in sorted(reply_counts.items(), key=lambda kv: kv[1], reverse=True)[:12]:
    m = by_msg.get(str(msg_id))
    if not m:
        continue
    text = m.get('text','')
    reason = 'got direct replies'
    low = text.lower()
    if any(w in low for w in ['lock', 'owner', 'rug', 'sam']): reason = 'polarizing owner/rug/lockup debate'
    elif any(w in low for w in ['mechanism', 'eval', 'miners', 'outputs']): reason = 'practical subnet mechanism discussion'
    elif any(w in low for w in ['revenue', 'business', 'buy back', 'money']): reason = 'real-world revenue angle'
    engagement.append({**m, 'reply_count_in_window': count, 'why_likely_engaged': reason})

compact = {
    'generated_at': raw.get('generated_at'),
    'window': raw.get('window'),
    'source': raw.get('source'),
    'stats': raw.get('stats'),
    'engagement_candidates': engagement,
    'messages_4h_chronological_trimmed': messages,
    'reply_anchor_candidates_recent_fuller': anchors,
}
print(json.dumps(compact, ensure_ascii=False, separators=(',', ':')))
PY
