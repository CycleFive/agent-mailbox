# Mailbox — store-and-forward triage for an agent

A pattern for letting an agent follow busy channels (GitHub repos,
Discord servers) **without paying inference to watch them**.

Built 2026-11-15 for Muse (Lothrop's Muse setup), after a
polling-based watch burned a week of another agent's usage budget in
three days. In its first day this system replaced 48 full agent scans
with 24 short triage runs, most finding nothing and ending in seconds.

> **Provenance:** this is an export of a running system, not a
> from-scratch library. It was extracted on 2026-11-18 from the setup
> where it had been operating since 2026-11-15 — the code is the live
> code, with credentials removed and Discord server IDs replaced by
> placeholders (see ADAPT.md). The worked example throughout is the
> [kharon](https://github.com/CycleFive/kharon) multi-agent build.

## The design

```
GitHub repo ───► github_broker.py ──────┐  (5-min delta poll)
                                        │
Discord ───────► gateway listener ──────┼──► inbox.jsonl ──► mail call
                 (+ REST fallback)      │    (append-only,    (hourly, one
                                        │     one record       agent session:
agent-bus ─────► stall / needs-owner ───┘     shape)           read past the
   events                                  cursor, act,
                                                   advance the cursor)
```

Three rules:

1. **Brokers collect; they never think.** Small deterministic programs
   poll the sources on a timer and append *normalized records* to an
   append-only inbox file. No model is involved, so polling is free.
2. **The agent reads mail in batches.** On a slow cadence (here:
   hourly) one agent session reads every record past its cursor,
   decides what needs action, acts, and advances the cursor — only
   over records it actually handled.
3. **Nothing pushes.** No event wakes the agent. Anything that arrives
   while the agent is asleep waits for the next mail call. Work that
   arrives while the agent is *already awake* is handled on the spot;
   the cadence is a hedge, not a preference.

The accepted cost is latency: up to one cadence interval (1 hour here)
for anything arriving while the agent is down.

### Why not webhooks or push?

Webhooks remove the polling, not the cost: every event still lands on
an always-on endpoint and, in an agent setup, still manufactures a
wake-up — and a wake-up is a model session, which is the expensive
part. Push has the same shape. Store-and-forward inverts it: delivery
is decoupled from attention. The brokers are dumb enough to run for
free forever, the agent reads on its own schedule (and tolerates
sleeping, moving hosts, or being mid-task), and a busy hour costs one
triage session instead of fifty interruptions. If your agent is
already always awake, you don't need this. If it isn't — or if its
attention is metered — you might.

## The pieces

| File | What it is |
|---|---|
| `mailbox.py` | The store. `append()` takes an flock and assigns `seq`; `read_since(cursor)`; cursor get/set in `triage-state.json`. The inbox is append-only JSONL; one record shape for every source: `{seq, ts_ingest, src, kind, id, ts, actor, ref, title, body, url, tags[]}`. |
| `github_broker.py` | GitHub broker. Delta-polls one repo's issues/PRs against a watermark (never re-reads history), skips the agent's own bot activity, tags records (`routed:review`, `routed:mention`, `routed:assigned`, `mine`), appends. Two side duties: mirrors unmirrored `needs_lothrop` bus flags to a Discord channel, and files a *stall record* when a claimed piece of work has no closing bus event after ~1 hour. On fetch failure it logs and does **not** advance the watermark. Has a dry-run mode that computes but writes nothing. |
| `discord/gateway_listener.py` | Discord broker, fast path. A persistent Gateway websocket listener that writes message/reaction events for watched channels to a local inbox file the moment they happen. |
| `discord/discord-muses-watch.sh` | Discord broker, glue + fallback. Consumes the gateway listener's inbox into the mailbox; if the gateway is down, falls back to bounded REST delta reads (after last seen id, ≤50/channel). Always ends silent — it is a broker, never a wake-up. |
| `discord/discord.py` | Small Discord REST helper (`send`, `messages`, reactions) used by the watch script and by the agent when it replies. Reads its bot token from a file beside it (`../private/token`). |
| `hooks/github-mail-broker.sh` | The scheduler wrapper for the GitHub broker. Written for Muse's hook runtime (`HATCH_HOOK_RUNTIME`); any timer (cron, systemd, a platform scheduler) that runs the broker every few minutes does the same job. |
| `bus/bus.py` | Optional: a session event bus (append-only JSONL of claim/blocked/needs_lothrop/resolved/done events) that the GitHub broker reads for its mirror + stall duties. Drop both duties if you don't want the bus. |
| `specs/mail-call-task.md` | The triage spec the hourly agent session runs: how records are classified, acted on, and booked; when the human hears about it (milestones, blockers, owner decisions — nothing else). |
| `specs/build-watch-task.md` | The worked example of per-source action rules (kharon-specific; replace with your own). |
| `sample-inbox.jsonl` | Three synthetic records showing the shapes. |

## Running it

1. Place `mailbox.py` and `github_broker.py` together; create the
   mailbox directory; the first append creates `inbox.jsonl`.
2. Give the brokers credentials (see ADAPT.md — none are included).
3. Schedule: GitHub broker every ~5 min, Discord watch every ~10 s
   (or just run the gateway listener as a service), mail call hourly.
4. The agent's mail-call session needs: read cursor → read records →
   act per your action rules → advance cursor over handled records →
   report only what your spec says is report-worthy.

## Invariants worth keeping

- **The cursor is single-consumer.** One agent (or one coordinated
  team) triages a given inbox. Two independent agents sharing one
  cursor *will* double-act — that failure mode is what this design
  replaced. A second agent needs its own cursor over the same stream,
  or its own inbox.
- **Advance the cursor only over handled records.** Mail that needs
  unfinished work stays unread, or is carried as an explicit open item.
- **Brokers fail closed.** A failed poll logs and keeps the old
  watermark; it never guesses and never advances past unseen data.
- **Records are pointers, not evidence.** The triage session fetches
  the full thread/diff before acting; the record just says where to
  look.
- **The inbox is append-only.** Never rewrite or rotate it without
  resetting every consumer's cursor.

## License

Licensed under either of

- Apache License, Version 2.0 ([LICENSE-APACHE](LICENSE-APACHE))
- MIT license ([LICENSE-MIT](LICENSE-MIT))

at your option — the same dual license as kharon, the project this
system was built to serve.
