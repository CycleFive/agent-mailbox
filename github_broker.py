#!/usr/bin/env python3
"""GitHub mail broker for CycleFive/kharon (built 2026-11-15).

Deterministic delta poller — no model involved. Each run:
  1. Fetches issues / PRs / issue comments / review comments / reviews via
     the gh-app helper and keeps only items past the watermark in
     activity-state.json (the same state file the old heartbeat watch used).
  2. Appends normalized records for other people's activity to the shared
     mailbox (~/workspace/mailbox/inbox.jsonl) with light mechanical tags
     (routed:review, routed:mention, mine).
  3. Mirrors open bus needs_lothrop flags to Discord #needs-lothrop (one
     post per ref, then a `mirrored` bus event) — previously a watch duty.
  4. Drops a mailbox record for suspected bus stalls (claim/start with no
     closing event after ~1h), at most once per ref per day.
  5. Advances the watermark. On any fetch failure it logs and leaves the
     watermark untouched, so the next poll retries the same window.

With HATCH_HOOK_DRY_RUN=1 it computes and prints what it would do but
writes nothing (no mailbox, watermark, Discord, or bus writes).
"""

import calendar
import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/hatch/workspace/mailbox")
import mailbox  # noqa: E402

REPO = "CycleFive/kharon"
HELPER = "/home/hatch/workspace/custom-connectors/custom.github-app/bin/gh-app-full.py"
DISCORD = "/home/hatch/workspace/custom-connectors/custom.discord/bin/discord.py"
BUS = "/home/hatch/workspace/agent-bus/bus.py"
EVENTS = Path("/home/hatch/workspace/agent-bus/events.jsonl")
STATE = Path("/home/hatch/workspace/goals/kharon-build-participation-nyx-lead/hidden_files/activity-state.json")
BST = Path("/home/hatch/workspace/mailbox/github-broker-state.json")
LOG = Path("/home/hatch/workspace/mailbox/github-broker.log")
BOT = "cycle-five-muse[bot]"
NEEDS_CHANNEL = "YOUR_NEEDS_CHANNEL_ID"
DRY = os.environ.get("HATCH_HOOK_DRY_RUN") == "1"


def log(msg: str) -> None:
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line)
    if not DRY:
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")


def api(path: str):
    r = subprocess.run([sys.executable, HELPER, "api", "GET", path],
                       capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        raise RuntimeError(f"helper failed on {path}: {r.stderr.strip()[:200]}")
    payload = json.loads(r.stdout)
    if payload.get("status", 200) >= 400:
        raise RuntimeError(f"api {payload.get('status')} on {path}")
    return payload["response"]


def excerpt(text: str, limit: int = 600) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[:limit] + " [...]"


def actor_of(item: dict) -> str:
    return ((item.get("user") or {}).get("login")) or ""


def main() -> int:
    wm = json.loads(STATE.read_text()) if STATE.exists() else {
        "last_issue_number": 0, "last_pr_number": 0, "last_issue_comment_id": 0,
        "last_review_comment_id": 0, "last_review_id": 0,
        "last_activity_check_at": "1970-01-01T00:00:00Z",
    }
    bst = json.loads(BST.read_text()) if BST.exists() else {"reported_stalls": {}}
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    records = []

    issues = api(f"/repos/{REPO}/issues?state=all&sort=updated&direction=desc&per_page=50")
    pulls = api(f"/repos/{REPO}/pulls?state=all&sort=updated&direction=desc&per_page=50")
    bot_prs = {p["number"] for p in pulls if actor_of(p) == BOT}

    for it in issues:
        if it.get("pull_request"):
            continue  # PRs arrive via the pulls stream
        if it["number"] > wm["last_issue_number"] and actor_of(it) != BOT:
            tags = []
            if any((a or {}).get("login") == BOT for a in (it.get("assignees") or [])):
                tags.append("routed:assigned")
            records.append({
                "src": "github", "kind": "issue", "id": f"gh:issue:{it['number']}",
                "ts": it.get("created_at"), "actor": actor_of(it),
                "ref": f"kharon#{it['number']}", "title": it.get("title", ""),
                "body": excerpt(it.get("body")), "url": it.get("html_url"), "tags": tags,
            })

    for pr in pulls:
        if pr["number"] > wm["last_pr_number"] and actor_of(pr) != BOT:
            tags = []
            labels = [(l or {}).get("name", "") for l in (pr.get("labels") or [])]
            if "review:muse" in labels:
                tags.append("routed:review")
            records.append({
                "src": "github", "kind": "pr", "id": f"gh:pr:{pr['number']}",
                "ts": pr.get("created_at"), "actor": actor_of(pr),
                "ref": f"kharon#{pr['number']}", "title": pr.get("title", ""),
                "body": excerpt(pr.get("body")), "url": pr.get("html_url"), "tags": tags,
            })

    icomments = api(f"/repos/{REPO}/issues/comments?sort=created&direction=desc&per_page=50")
    for c in icomments:
        if c["id"] > wm["last_issue_comment_id"] and actor_of(c) != BOT:
            num = (c.get("issue_url") or "").rstrip("/").split("/")[-1]
            tags = ["mine"] if num.isdigit() and int(num) in bot_prs else []
            if "cycle-five-muse" in (c.get("body") or ""):
                tags.append("routed:mention")
            records.append({
                "src": "github", "kind": "issue_comment", "id": f"gh:ic:{c['id']}",
                "ts": c.get("created_at"), "actor": actor_of(c),
                "ref": f"kharon#{num}", "title": "",
                "body": excerpt(c.get("body")), "url": c.get("html_url"), "tags": tags,
            })

    rcomments = api(f"/repos/{REPO}/pulls/comments?sort=created&direction=desc&per_page=50")
    for c in rcomments:
        if c["id"] > wm["last_review_comment_id"] and actor_of(c) != BOT:
            num = (c.get("pull_request_url") or "").rstrip("/").split("/")[-1]
            tags = ["mine"] if num.isdigit() and int(num) in bot_prs else []
            records.append({
                "src": "github", "kind": "review_comment", "id": f"gh:rc:{c['id']}",
                "ts": c.get("created_at"), "actor": actor_of(c),
                "ref": f"kharon#{num}", "title": "",
                "body": excerpt(c.get("body")), "url": c.get("html_url"), "tags": tags,
            })

    seen_review_ids = []
    for pr in pulls:
        if (pr.get("updated_at") or "") <= wm["last_activity_check_at"]:
            continue
        for rv in api(f"/repos/{REPO}/pulls/{pr['number']}/reviews?per_page=50"):
            seen_review_ids.append(rv["id"])
            if rv["id"] > wm["last_review_id"] and actor_of(rv) != BOT:
                tags = ["mine"] if pr["number"] in bot_prs else []
                records.append({
                    "src": "github", "kind": "review", "id": f"gh:review:{rv['id']}",
                    "ts": rv.get("submitted_at"), "actor": actor_of(rv),
                    "ref": f"kharon#{pr['number']}", "title": rv.get("state", ""),
                    "body": excerpt(rv.get("body")), "url": rv.get("html_url"), "tags": tags,
                })

    # --- Bus duties: mirror needs_lothrop, flag stalls.
    events = []
    if EVENTS.exists():
        for line in EVENTS.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    mirrored_refs = {e.get("ref") for e in events if e.get("kind") == "mirrored"}
    resolved_refs = {e.get("ref") for e in events if e.get("kind") == "resolved"}
    for e in events:
        if e.get("kind") != "needs_lothrop":
            continue
        ref = e.get("ref")
        if ref in mirrored_refs or ref in resolved_refs:
            continue
        text = (e.get("needs") or e.get("note") or "").strip()
        msg = f"Needs Lothrop: {text}" if text else f"Needs Lothrop: {ref}"
        if DRY:
            log(f"would mirror needs_lothrop {ref} to Discord")
            continue
        r = subprocess.run([sys.executable, DISCORD, "send", NEEDS_CHANNEL, msg],
                           capture_output=True, text=True, timeout=60)
        if r.returncode == 0:
            subprocess.run([sys.executable, BUS, "add", "--who", "broker",
                            "--kind", "mirrored", "--ref", ref or "",
                            "--note", "posted to #needs-lothrop by github broker"],
                           capture_output=True, text=True, timeout=60)
            mirrored_refs.add(ref)
            log(f"mirrored needs_lothrop {ref}")
        else:
            log(f"mirror post failed for {ref}: {r.stderr.strip()[:160]}")

    now_epoch = time.time()
    latest_open = {}
    for e in events:
        ref = e.get("ref")
        if not ref:
            continue
        if e.get("kind") in ("claim", "start"):
            latest_open[ref] = e
        elif e.get("kind") in ("done", "error", "blocked", "needs_lothrop", "resolved"):
            latest_open.pop(ref, None)
    today = time.strftime("%Y-%m-%d", time.gmtime())
    for ref, e in latest_open.items():
        try:
            ts = time.strptime(e.get("ts", ""), "%Y-%m-%dT%H:%M:%SZ")
            age = now_epoch - calendar.timegm(ts)  # event ts is UTC
        except (ValueError, TypeError):
            continue
        if age > 3600 and bst["reported_stalls"].get(ref) != today:
            records.append({
                "src": "bus", "kind": "stall", "id": f"bus:stall:{ref}:{today}",
                "ts": now, "actor": e.get("who", ""), "ref": ref,
                "title": "suspected stall (claim/start >1h, no closing event)",
                "body": excerpt(e.get("note")), "url": "", "tags": ["stall"],
            })
            bst["reported_stalls"][ref] = today

    # --- Deliver + advance watermark.
    if DRY:
        log(f"dry run: {len(records)} record(s) would be appended")
        for rec in records:
            log(f"  would append {rec['id']} {rec['ref']} tags={rec['tags']}")
        return 0
    for rec in records:
        mailbox.append(rec)
    new_wm = dict(wm)
    new_wm["last_issue_number"] = max([wm["last_issue_number"]] +
                                      [i["number"] for i in issues if not i.get("pull_request")])
    new_wm["last_pr_number"] = max([wm["last_pr_number"]] + [p["number"] for p in pulls])
    new_wm["last_issue_comment_id"] = max([wm["last_issue_comment_id"]] + [c["id"] for c in icomments])
    new_wm["last_review_comment_id"] = max([wm["last_review_comment_id"]] + [c["id"] for c in rcomments])
    new_wm["last_review_id"] = max([wm["last_review_id"]] + seen_review_ids)
    new_wm["last_activity_check_at"] = now
    STATE.write_text(json.dumps(new_wm))
    BST.write_text(json.dumps(bst))
    log(f"poll ok: {len(records)} record(s) appended; watermark at issue/PR {new_wm['last_issue_number']}/{new_wm['last_pr_number']}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # never advance state on failure
        log(f"broker error: {exc}")
        sys.exit(1)
