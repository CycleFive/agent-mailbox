#!/usr/bin/env python3
"""Shared mailbox store for the broker/mail-call setup (built 2026-11-15).

Brokers (GitHub delta poller, Discord gateway hook) append normalized
records to ~/workspace/mailbox/inbox.jsonl. The hourly mail-call session
reads records after its cursor in triage-state.json and acts on them.

append() is safe for concurrent writers: it takes an flock on the inbox
file and assigns `seq` (1-based line number) under the lock. The inbox is
append-only; never rewrite or rotate it without resetting the triage
cursor.
"""

import fcntl
import json
import time
from pathlib import Path

MAILBOX = Path("/home/hatch/workspace/mailbox")
INBOX = MAILBOX / "inbox.jsonl"
TRIAGE_STATE = MAILBOX / "triage-state.json"


def append(record: dict) -> dict:
    """Append one record, assigning seq + ts_ingest. Returns the record."""
    MAILBOX.mkdir(parents=True, exist_ok=True)
    with open(INBOX, "a+", encoding="utf-8") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        f.seek(0)
        seq = sum(1 for _ in f)
        record = dict(record)
        record["seq"] = seq + 1
        record.setdefault("ts_ingest", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()
    return record


def read_since(cursor: int) -> list:
    """All records with seq > cursor (cursor = last processed seq)."""
    if not INBOX.exists():
        return []
    out = []
    with open(INBOX, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            if rec.get("seq", 0) > cursor:
                out.append(rec)
    return out


def get_cursor() -> int:
    if TRIAGE_STATE.exists():
        return json.loads(TRIAGE_STATE.read_text()).get("processed_seq", 0)
    return 0


def set_cursor(seq: int) -> None:
    MAILBOX.mkdir(parents=True, exist_ok=True)
    TRIAGE_STATE.write_text(json.dumps({
        "processed_seq": seq,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }))
