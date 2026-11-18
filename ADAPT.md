# ADAPT — every environment-specific seam

This is a faithful copy of a running system (Muse's, on Lothrop's
setup), not a generalized library. Everything below must be replaced
or re-pointed for a new agent/host. **No credentials are included**:
no Discord bot token, no GitHub App key — both are file-held in the
original and were deliberately left out. Discord guild/channel/user
IDs in this copy are `YOUR_*` placeholders (the original server is
private); kharon references stay, as that project is public and is
the worked example.

## Paths (all absolute, `/home/hatch/...`)

- `mailbox.py`: `MAILBOX` constant — the mailbox directory.
- `github_broker.py` header constants: `HELPER` (GitHub API helper),
  `DISCORD` (Discord helper), `BUS` / `EVENTS` (event bus), `STATE`
  (activity watermark file), `BST` (broker state), `LOG`.
- `discord/gateway_listener.py`: `STATE` (its seen-ids directory).
- `discord/discord.py`: token path is relative to the file
  (`../private/token`) — keep the layout or change the constant.

## Identity

- `github_broker.py`: `BOT = "cycle-five-muse[bot]"` — the broker
  skips activity authored by the agent's own bot account, and tags
  records mentioning it. Set this to the new agent's GitHub identity
  (or `None` to keep everything).
- The GitHub broker talks to the API through a helper that mints a
  **GitHub App installation token** (`gh-app-full.py`, not included —
  it belongs to a separate connector). Any authenticated GitHub API
  client works in its place; the broker only needs issue/PR/comment/
  review reads for one repo. Expect to rewrite the fetch functions
  against your client; the watermark + tagging + append logic is the
  part worth keeping.
- The Discord side expects a **bot token in a file** (dir 700, file
  600). The new agent needs its own bot application (or an invite of
  an existing one) with the MESSAGE_CONTENT intent and reaction
  events enabled.

## Channels and repo

- `github_broker.py`: `NEEDS_CHANNEL` — the Discord channel that
  owner-decision flags get mirrored to (here: a `#needs-lothrop`
  channel). Also the repo under watch is set in the broker's fetch
  section (CycleFive/kharon).
- `gateway_listener.py`: `GUILD_ID` + the watched-channel list.
- `discord-muses-watch.sh`: the channel spec list it falls back over,
  and `BOT_ID` (used to skip the bot's own messages).

## Scheduling (reference only)

The original runs on Muse's runtime: two *hooks* (poll scripts
that must end silent) and one hourly *cron* whose body is just a
pointer to `specs/mail-call-task.md`. None of that plumbing is
portable or needs to be — reproduce the cadences (GitHub ~5 min,
Discord ~10 s or the always-on listener, mail call ~1 h) on whatever
scheduler the new host has.

## The specs are examples, not defaults

`specs/mail-call-task.md` is generic in shape (cursor discipline,
report-only-on-milestone/blocker/decision) but its action rules point
at `specs/build-watch-task.md`, which is entirely kharon team
protocol: claim comments, review labels, REQ IDs, named humans. Keep
the shape; write your own action rules.

## Deliberately excluded

- Live state: `inbox.jsonl` contents, cursors, watermarks, logs.
- Credentials: Discord token file, GitHub App PEM, any vault material.
- The gh-app helper and the rest of the connector collection.
- Muse's memory/persona files — the mailbox doesn't depend on them;
  the triage spec's judgment calls do, and the new agent will grow
  its own.
