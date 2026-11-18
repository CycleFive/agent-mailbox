#!/usr/bin/env bash
set -euo pipefail
source "$HATCH_HOOK_RUNTIME"

# GitHub mail broker: deterministic delta poll of CycleFive/kharon.
# Appends new activity to ~/workspace/mailbox/inbox.jsonl, mirrors bus
# needs_lothrop flags to Discord, flags stalls. NEVER wakes an agent —
# the hourly mail-call cron triages the mailbox.
out="$(python3 "$HOME/workspace/mailbox/github_broker.py" 2>&1 | tail -3)" || true
log "github broker poll" "$(printf '%s' "$out" | python3 -c 'import json,sys; print(json.dumps({"tail": sys.stdin.read()}))')"
silent "broker poll complete" '{}'
