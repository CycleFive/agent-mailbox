#!/usr/bin/env bash
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

HELPER="$HOME/workspace/custom-connectors/custom.discord/bin/discord.py"
BOT_ID="1566796973611294750"
STATE_DIR="$HOME/hooks/state"
INBOX="$STATE_DIR/discord-inbox.jsonl"
HEARTBEAT="$STATE_DIR/discord-gateway-heartbeat"
SEEN="$STATE_DIR/discord-gateway-seen.json"
API_STAMP="$STATE_DIR/discord-api-fallback-stamp"
BOOTFILE="$STATE_DIR/discord-gateway-boot"
CATCHUP_DONE="$STATE_DIR/discord-catchup-done"
UNIT_SRC="$HOME/workspace/custom-connectors/custom.discord/systemd/discord-gateway.service"
UNIT_DST="/etc/systemd/system/discord-gateway.service"

# --- Mailbox drop-off (mailbox design, 2026-11-15): this broker never
# wakes an agent. Normalized records go to ~/workspace/mailbox/inbox.jsonl
# and the hourly mail-call cron triages them. In dry-run mode it only
# counts what it would drop.
drop_mail() {  # stdin: JSON array of inbox-shaped records; stdout: count
  python3 -c '
import json, os, sys
lines = json.load(sys.stdin)
if os.environ.get("HATCH_HOOK_DRY_RUN") == "1":
    print(len(lines)); sys.exit(0)
sys.path.insert(0, "/home/hatch/workspace/mailbox")
import mailbox
n = 0
for m in lines:
    mailbox.append({
        "src": "discord",
        "kind": m.get("kind", "message"),
        "id": "dc:" + str(m["id"]),
        "ts": m.get("timestamp"),
        "actor": m.get("author", ""),
        "ref": "#" + str(m.get("channel", "")),
        "title": "",
        "body": (m.get("content") or "")[:2000],
        "url": "",
        "tags": [],
    })
    n += 1
print(n)
'
}

# --- Gateway watchdog: keep the listener alive (reinstall after VM
# replacement, restart if down). Cheap checks; failures only log.
if [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
  if [ ! -f "$UNIT_DST" ] && [ -f "$UNIT_SRC" ]; then
    cp "$UNIT_SRC" "$UNIT_DST" 2>/dev/null && systemctl daemon-reload 2>/dev/null \
      && systemctl enable discord-gateway.service 2>/dev/null \
      && log "gateway unit reinstalled from workspace copy" '{}' || true
  fi
  if ! systemctl is-active --quiet discord-gateway.service 2>/dev/null; then
    systemctl start discord-gateway.service 2>/dev/null \
      && log "gateway service started by watchdog" '{}' \
      || log "gateway service start failed" '{}'
  fi
fi

# --- Catch-up check: a fresh gateway READY (listener was down and
# re-identified, so Discord did not replay what it missed) leaves a boot
# marker. Until one REST sweep has run past it, force the sweep below even
# when the gateway is healthy again.
FORCE=0
if [ -f "$BOOTFILE" ] && { [ ! -f "$CATCHUP_DONE" ] || [ "$BOOTFILE" -nt "$CATCHUP_DONE" ]; }; then
  FORCE=1
fi

# --- Fast path: consume the gateway inbox.
if [ -s "$INBOX" ]; then
  proc="$INBOX.processing.$$"
  mv "$INBOX" "$proc"
  fresh="$(python3 -c '
import json, sys
lines = []
with open(sys.argv[1], encoding="utf-8") as f:
    for line in f:
        line = line.strip()
        if line:
            lines.append(json.loads(line))
print(json.dumps({"fresh": lines}))
' "$proc")"
  # Advance the REST watermarks to the newest gateway-seen ids so the
  # fallback path never replays what the gateway already delivered.
  if [ -f "$SEEN" ] && [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
    python3 -c '
import json, sys
seen = json.load(open(sys.argv[1]))
names = {"1566796120426815540": "discord-muses-last-id",
         "1566884216690704646": "discord-muses-protocol-last-id",
         "1567978974653583420": "discord-muses-minplus-last-id",
         "1570159188867289088": "discord-muses-needs-last-id"}
import os
for ch, base in names.items():
    if ch in seen:
        p = os.path.join(sys.argv[2], base)
        cur = ""
        if os.path.exists(p):
            cur = open(p).read().strip()
        if not cur or int(seen[ch]) > int(cur):
            open(p, "w").write(seen[ch])
' "$SEEN" "$STATE_DIR" || true
  fi
  fresh_lines="$(printf '%s' "$fresh" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["fresh"]))')"
  if dropped="$(printf '%s' "$fresh_lines" | drop_mail)"; then
    if [ "${HATCH_HOOK_DRY_RUN:-0}" = "1" ]; then
      cat "$proc" >> "$INBOX" 2>/dev/null || true  # dry run: restore, drop nothing
    fi
    rm -f "$proc"
    silent "dropped $dropped discord record(s) in mailbox (gateway)" '{}'
  else
    cat "$proc" >> "$INBOX" 2>/dev/null || true
    rm -f "$proc"
    log "mailbox drop failed; gateway inbox restored for retry" '{}'
    silent "mailbox drop failed, will retry next poll" '{}'
  fi
fi

# --- Gateway alive and quiet: stay silent (unless a catch-up is owed).
now="$(date +%s)"
if [ "$FORCE" -eq 0 ] && [ -f "$HEARTBEAT" ]; then
  hb_age=$(( now - $(stat -c %Y "$HEARTBEAT") ))
  if [ "$hb_age" -lt 120 ]; then
    silent "gateway alive, inbox empty" "{\"heartbeat_age_s\":$hb_age}"
  fi
fi

# --- Fallback: REST polling (gateway down). Throttled to ~100 s between
# sweeps even though this hook now runs every 10 s.
if [ "$FORCE" -eq 0 ] && [ -f "$API_STAMP" ]; then
  api_age=$(( now - $(stat -c %Y "$API_STAMP") ))
  if [ "$api_age" -lt 100 ]; then
    silent "gateway down, REST fallback throttled" "{\"api_age_s\":$api_age}"
  fi
fi
[ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ] && touch "$API_STAMP"

SPECS=(
  "1566796120426815540:general:discord-muses-last-id"
  "1566884216690704646:protocol:discord-muses-protocol-last-id"
  "1567978974653583420:minplus-book:discord-muses-minplus-last-id"
  "1570159188867289088:needs-lothrop:discord-muses-needs-last-id"
)

all_fresh="[]"
any_fetch=0

for spec in "${SPECS[@]}"; do
  IFS=':' read -r channel name statebase <<< "$spec"
  state="$STATE_DIR/$statebase"
  last=""
  [ -f "$state" ] && last="$(cat "$state")"

  if [ -z "$last" ]; then
    newest="$(python3 "$HELPER" messages "$channel" 1 | python3 -c 'import json,sys; m=json.load(sys.stdin); print(m[0]["id"] if m else "")')"
    if [ -n "$newest" ] && [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
      printf '%s' "$newest" > "$state"
    fi
    continue
  fi

  raw="$(python3 "$HELPER" messages "$channel" 50 "$last" || true)"
  if [ -z "$raw" ]; then
    log "fetch failed or empty for #$name" '{}'
    continue
  fi
  any_fetch=1

  result="$(printf '%s' "$raw" | BOT_ID="$BOT_ID" CHANNEL_ID="$channel" CHANNEL_NAME="$name" python3 -c '
import json, os, sys
msgs = json.load(sys.stdin)
bot = os.environ["BOT_ID"]
newest = msgs[0]["id"] if msgs else ""
fresh = [m for m in reversed(msgs) if m["author"]["id"] != bot]
lines = [{"id": m["id"], "channel_id": os.environ["CHANNEL_ID"], "channel": os.environ["CHANNEL_NAME"], "author": (m["author"].get("global_name") or m["author"]["username"]), "content": m["content"], "timestamp": m["timestamp"]} for m in fresh]
print(json.dumps({"newest": newest, "fresh": lines}))
')"
  newest="$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.load(sys.stdin)["newest"])')"
  if [ -n "$newest" ] && [ "${HATCH_HOOK_DRY_RUN:-0}" != "1" ]; then
    printf '%s' "$newest" > "$state"
  fi
  fresh_part="$(printf '%s' "$result" | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["fresh"]))')"
  all_fresh="$(printf '%s' "$all_fresh" | FRESH="$fresh_part" python3 -c '
import json, os, sys
base = json.load(sys.stdin)
extra = json.loads(os.environ["FRESH"])
print(json.dumps(base + extra))
')"
done

# A completed sweep settles any owed catch-up.
if [ "$FORCE" -eq 1 ] && [ "$any_fetch" -eq 1 ]; then
  touch "$CATCHUP_DONE"
fi

if [ "$any_fetch" -eq 0 ] && [ "$all_fresh" = "[]" ]; then
  silent "fetch inconclusive or watermarks initialised" '{}'
fi

count="$(printf '%s' "$all_fresh" | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')"
if [ "$count" -eq 0 ]; then
  silent "no new messages from others (REST fallback)" '{}'
fi

dropped="$(printf '%s' "$all_fresh" | drop_mail)" || dropped="?"
silent "dropped $dropped discord record(s) in mailbox (REST fallback)" '{}'
