# Mail call — hourly triage spec (replaced the heartbeat kharon watch 2026-11-15)

Lothrop's design: no agent polls GitHub or Discord and nothing pushes
wake-ups. Two deterministic brokers drop normalized records into
~/workspace/mailbox/inbox.jsonl — the GitHub broker (hook
`github-mail-broker`, 5-min delta poll via the gh-app helper; also mirrors
bus needs_lothrop flags to #needs-lothrop and files stall records) and the
Discord broker (the `discord-muses-watch` hook consuming the gateway
listener's inbox; messages and reactions). This session wakes once an
hour, reads its mail, and decides what needs interaction.

## Each run

1. Read the triage cursor (~/workspace/mailbox/triage-state.json,
   `processed_seq`; helper functions in ~/workspace/mailbox/mailbox.py)
   and fetch all inbox records with a higher seq. Also run
   `python3 ~/workspace/agent-bus/bus.py open`.
2. If there are no new records and no open bus flags: end quietly. No
   message to Lothrop, no log spam — append nothing anywhere.
3. Otherwise triage every record:
   - **GitHub records** (src=github): act per the ACTION rules in
     build-watch-task.md (same directory) — claim routed work publicly
     first, do reviews properly with the review:<agent> label protocol,
     respond on my in-flight PRs/issues, cite REQ IDs, follow the
     Lothrop Muses protocol (GitHub is the record; stay on the path;
     parked stays parked). Records tagged routed:* or mine are the
     priority; untagged records are context — read them so the next
     decision is informed, but they rarely need action. Fetch full
     threads/diffs via the gh-app helper as needed; the mailbox record
     is a pointer, not the evidence.
   - **Discord records** (src=discord): follow the ack protocol — answer
     messages addressed to Muse, or react ✅ received / 👀 working via
     the Discord helper; never ack an ack. Kharon actions taken because
     of a Discord message still happen on GitHub (the record), with the
     one-line Discord note the protocol calls for. Nyx pings for kharon
     reviews per build-watch-task.md.
   - **Reaction records** (kind=reaction): a ✅ from Lothrop on a
     #needs-lothrop item resolves the matching bus flag — append the
     `resolved` event (same ref) and, if the flag was waiting on his go
     for work I own, proceed with that work in this run.
   - **Stall records** (src=bus, kind=stall): verify before concluding —
     check GitHub and the filesystem, and check memory/goal notes for a
     recorded scoping (e.g. work deliberately waiting on Lothrop's
     method session is NOT stalled). Surface a genuine stall to Lothrop
     as a blocker; dismiss a scoped one in the run's bookkeeping only.
4. Bookkeeping as you go: bus events for claim/done/error/blocked/
   needs_lothrop per the AGENTS.md convention; a one-line activity entry
   on tracked goal goal_f4406d3d99a1 for completed routed work; a brief
   entry in the daily log ~/memory/<today>.md when anything was acted on.
5. Advance the triage cursor ONLY over records you actually processed
   (mailbox.set_cursor). If a record needs work that can't finish this
   run, leave the cursor before it or carry it as an explicit open item
   in the daily log — never mark mail read that wasn't handled.
6. Report to Lothrop in the main chat ONLY for: a milestone completed, a
   blocker the team can't resolve, an owner-level decision, or a
   substantive disagreement that survived two exchanges (same rule as
   the old watch). Everything else stays on GitHub/Discord and the
   tracked item.

Identity and safety rules are unchanged from build-watch-task.md: all
kharon GitHub work through the gh-app helper as cycle-five-muse[bot];
never merge unless the team's process assigns it and Nyx approved;
live systems (deploys, credentials, anything public-facing) wait for
Lothrop's go in #needs-lothrop.
