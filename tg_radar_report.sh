#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
PYTHON_BIN="${PYTHON:-python3}"
TG_MONITOR_DB="${TG_MONITOR_DB:-${DB_PATH:-}}"
DB_ARGS=()
if [[ -n "$TG_MONITOR_DB" ]]; then
  DB_ARGS=(--db "$TG_MONITOR_DB")
fi
RAW=/tmp/tg-hot-topics-context.json
COMPACT=/tmp/tg-hot-topics-compact.json
PROMPT=/tmp/tg-hot-topics-prompt.txt
./tg_hot_topics_context.py "${DB_ARGS[@]}" --hours 4 --reply-recency-minutes 90 --output "$RAW"
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
        item['reply_parent'] = {
            'sender': parent.get('sender'),
            'text': trim(parent.get('text'), 320),
        }
    anchors.append(item)

compact = {
    'generated_at': raw.get('generated_at'),
    'window': raw.get('window'),
    'source': raw.get('source'),
    'stats': raw.get('stats'),
    'messages_4h_chronological_trimmed': messages,
    'reply_anchor_candidates_recent_fuller': anchors,
}
Path('/tmp/tg-hot-topics-compact.json').write_text(json.dumps(compact, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
PY
cat > "$PROMPT" <<'EOF'
You are writing the Telegram hot-topic radar for Discord.

Use only the JSON below. Analyze the last 4 hours, explain the discussion in Georgian, and suggest replies only for active/recent threads. Do not answer old or disconnected messages.

Rules:
- Hot topics can use the full 4h message list.
- Suggested replies must anchor to reply_anchor_candidates_recent_fuller, ideally last 60 to 90 minutes.
- Include each anchor as `↪ msg <msg_id>, <sender>, <age>m ago`.
- Skip stale fights, closed threads, jokes, and small talk.
- Tone: concise, direct, natural group-chat style, no corporate wording, no hype.
- Georgian explanation required.
- Suggested reply texts can be English if the Telegram discussion is English.
- No em dashes.
- No markdown bold.
- No horizontal separators.

Output exactly:
📡 TG Radar — last 4h

Hot topics:
• <topic>: <what users are saying, why it matters now>
• <topic>: <what users are saying, why it matters now>
• <topic>: <what users are saying, why it matters now>

დისკუსიის ახსნა ქართულად:
• <ქართული ახსნა>
• <ქართული ახსნა>
• <ქართული ახსნა: რა არის ძველი ან არ ღირს პასუხად>

Suggested replies:
1. ↪ msg <id>, <sender>, <age>m ago
<reply text, max 2 sentences>
Why: <short reason, Georgian or English>

2. ↪ msg <id>, <sender>, <age>m ago
<reply text, max 2 sentences>
Why: <short reason, Georgian or English>

3. ↪ msg <id>, <sender>, <age>m ago
<reply text, max 2 sentences>
Why: <short reason, Georgian or English>

JSON:
EOF
cat "$COMPACT" >> "$PROMPT"
openclaw infer model run --model openai-codex/gpt-5.5 --prompt "$(cat "$PROMPT")" --json \
  | jq -r '.outputs[0].text // empty'
