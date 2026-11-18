#!/usr/bin/env python3
"""Session event bus — append-only JSONL shared by all Muse sessions on this host.

Every session (main chat, heartbeat/watch runs, event-hook passes, background
workers) appends one JSON object per line to events.jsonl:

    {"ts": "2026-11-11T18:52:00Z", "who": "main", "kind": "claim",
     "ref": "kharon#247", "note": "fixing the grant-rows wait"}

Fields: ts (UTC, added automatically), who (short session label: main, watch,
hook:discord, a goal slug, ...), kind, ref (issue/PR/item the event is about),
note (short free text, e.g. a link), needs (for kind=needs_lothrop only: what
Lothrop has to decide or do).

Kinds: start, claim, progress, blocked, needs_lothrop, resolved, done, error,
note, mirrored.

Semantics that matter:
- needs_lothrop raises a flag keyed by ref; a later `resolved` event with the
  same ref clears it. Nothing else clears it.
- `mirrored` (same ref) records that a needs_lothrop flag was posted to the
  Discord #needs-lothrop channel, so it is never posted twice.
- A torn or malformed line is skipped by readers, never fatal.

Usage:
    bus.py add --who main --kind claim --ref kharon#247 --note "..."
    bus.py add --who watch --kind needs_lothrop --ref kharon#208 \
        --needs "go to publish kharon-owner.twkr.io" --note "https://..."
    bus.py tail [-n 20]
    bus.py open        # unresolved needs_lothrop flags

The log defaults to events.jsonl beside this script; --file overrides it
(used for testing). Appends are a single O_APPEND write of one line.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timezone

DEFAULT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "events.jsonl")
KINDS = {
    "start", "claim", "progress", "blocked", "needs_lothrop", "resolved",
    "done", "error", "note", "mirrored",
}


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_events(path):
    events = []
    if not os.path.exists(path):
        return events
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except Exception:
                continue  # tolerate a torn line; never fatal
    return events


def open_flags(events):
    """Unresolved needs_lothrop flags, keyed by ref, oldest first."""
    flags = {}
    for ev in events:
        ref = ev.get("ref")
        if not ref:
            continue
        if ev.get("kind") == "needs_lothrop":
            flags[ref] = ev
        elif ev.get("kind") == "resolved":
            flags.pop(ref, None)
    return flags


def mirrored_refs(events):
    return {ev.get("ref") for ev in events if ev.get("kind") == "mirrored"}


def cmd_add(args):
    if args.kind not in KINDS:
        print(f"unknown kind {args.kind!r}; expected one of {sorted(KINDS)}", file=sys.stderr)
        return 2
    ev = {"ts": utcnow(), "who": args.who, "kind": args.kind}
    if args.ref:
        ev["ref"] = args.ref
    if args.note:
        ev["note"] = args.note
    if args.needs:
        ev["needs"] = args.needs
    line = json.dumps(ev) + "\n"
    fd = os.open(args.file, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)
    print(line, end="")
    return 0


def cmd_tail(args):
    events = read_events(args.file)
    for ev in events[-args.n:]:
        print(json.dumps(ev))
    return 0


def cmd_open(args):
    events = read_events(args.file)
    flags = open_flags(events)
    mirrored = mirrored_refs(events)
    if not flags:
        print("(no open needs_lothrop flags)")
        return 0
    for ref, ev in flags.items():
        mark = "mirrored" if ref in mirrored else "UNMIRRORED"
        print(f"{ref} | {ev.get('ts')} | {ev.get('who')} | {mark} | "
              f"needs: {ev.get('needs', '')} | {ev.get('note', '')}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="Session event bus")
    ap.add_argument("--file", default=DEFAULT_FILE, help="events file (default: %(default)s)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--who", required=True)
    p_add.add_argument("--kind", required=True)
    p_add.add_argument("--ref")
    p_add.add_argument("--note")
    p_add.add_argument("--needs")
    p_add.set_defaults(fn=cmd_add)

    p_tail = sub.add_parser("tail")
    p_tail.add_argument("-n", type=int, default=20)
    p_tail.set_defaults(fn=cmd_tail)

    p_open = sub.add_parser("open")
    p_open.set_defaults(fn=cmd_open)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
