#!/usr/bin/env python3
"""Discord bot helper for the Muse bot (custom.discord).

Credential: the bot token lives OUTSIDE the Muse vault by Lothrop's explicit
exception (2026-11-02, same pattern as the kharon GitHub App PEM): the API-key
capture flow cannot validate Discord's `Authorization: Bot <token>` scheme.
Token file: ../private/token (dir 700, file 600). It is read at call time,
never printed, never logged, never passed as a command-line argument.
Rotation: reset the token in the Discord Developer Portal, replace the file.

Usage:
  discord.py me
  discord.py guilds
  discord.py channels <guild_id>
  discord.py messages <channel_id> [limit] [after_message_id]
  discord.py send <channel_id> <message text...>
"""
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

API = "https://discord.com/api/v10"
TOKEN_FILE = Path(__file__).resolve().parent.parent / "private" / "token"


def load_token() -> str:
    raw = TOKEN_FILE.read_text(encoding="utf-8").strip()
    if raw.lower().startswith("bot "):
        raw = raw[4:].strip()
    if not raw:
        raise SystemExit("token file is empty")
    return raw


def api(method: str, path: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bot {load_token()}")
    req.add_header("User-Agent", "DiscordBot (https://minplus.xyz, 1.0)")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8") or "null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} on {method} {path}: {detail}", file=sys.stderr)
        raise SystemExit(1)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "me":
        out = api("GET", "/users/@me")
    elif cmd == "guilds":
        out = api("GET", "/users/@me/guilds")
    elif cmd == "channels" and len(args) == 1:
        out = api("GET", f"/guilds/{args[0]}/channels")
    elif cmd == "messages" and 1 <= len(args) <= 3:
        q = f"?limit={args[1] if len(args) > 1 else 25}"
        if len(args) > 2:
            q += f"&after={args[2]}"
        out = api("GET", f"/channels/{args[0]}/messages{q}")
    elif cmd == "send" and len(args) >= 2:
        out = api("POST", f"/channels/{args[0]}/messages",
                  {"content": " ".join(args[1:])})
    elif cmd == "react" and len(args) == 3:
        # PUT returns 204 No Content, which api() parses as null.
        emoji = urllib.parse.quote(args[2], safe="")
        out = api("PUT", f"/channels/{args[0]}/messages/{args[1]}"
                         f"/reactions/{emoji}/@me")
        if out is None:
            out = {"reacted": args[2], "message_id": args[1]}
    else:
        print(__doc__)
        return 2
    print(json.dumps(out, indent=1, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
