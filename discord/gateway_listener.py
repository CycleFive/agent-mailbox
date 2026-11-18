#!/usr/bin/env python3
"""Discord Gateway listener for the Muse bot (custom.discord).

Maintains a persistent Gateway WebSocket connection and appends every
MESSAGE_CREATE in the watched Lothrop Muses channels to a local inbox file
(~/hooks/state/discord-inbox.jsonl), which the discord-muses-watch hook
consumes every few seconds — near-real-time wake instead of the 120 s REST
poll. Also maintains:
  - discord-gateway-heartbeat: touched on every heartbeat send, so the hook
    can tell whether the gateway path is alive and only fall back to REST
    polling when it is not.
  - discord-gateway-seen.json: newest message id seen per watched channel
    (including the bot's own messages), used to advance hook watermarks.
  - discord-gateway-boot: rewritten on every fresh READY (not on RESUME —
    a resumed session replays what was missed; a fresh identify does not).
    The hook runs one REST catch-up sweep when it sees a boot marker it
    has not consumed, closing the gap left by listener downtime (e.g. a
    VM replacement reboot, observed 2026-11-02 16:58: ~30 s out).

Credential: reads the bot token from ../private/token at startup, the same
file exception Lothrop approved for discord.py (2026-11-02). The token is
never logged or written anywhere. Run under the discord-gateway systemd
user service; logs go to the journal.
"""

import asyncio
import base64
import json
import os
import socket
import sys
import time
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from discord import load_token  # noqa: E402  (reads ../private/token)

import websockets  # noqa: E402

STATE = Path("/home/hatch/hooks/state")
INBOX = STATE / "discord-inbox.jsonl"
HEARTBEAT = STATE / "discord-gateway-heartbeat"
SEEN = STATE / "discord-gateway-seen.json"
BOOT = STATE / "discord-gateway-boot"

GUILD_ID = "1566796119839875222"
WATCHED = {
    "1566796120426815540": "general",
    "1566884216690704646": "protocol",
    "1567978974653583420": "minplus-book",
    "1570159188867289088": "needs-lothrop",
}
BOT_ID = "1566796973611294750"
GATEWAY = "wss://gateway.discord.gg/?v=10&encoding=json"
# GUILDS | GUILD_MESSAGES | GUILD_MESSAGE_REACTIONS | MESSAGE_CONTENT
INTENTS = 1 | 512 | 1024 | 32768


def log(msg: str) -> None:
    print(f"[gateway] {msg}", flush=True)


def append_inbox(record: dict) -> None:
    line = json.dumps(record, ensure_ascii=False) + "\n"
    fd = os.open(INBOX, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def update_seen(channel_id: str, message_id: str) -> None:
    try:
        seen = json.loads(SEEN.read_text()) if SEEN.exists() else {}
    except Exception:
        seen = {}
    if int(message_id) > int(seen.get(channel_id, "0")):
        seen[channel_id] = message_id
        tmp = SEEN.with_suffix(".tmp")
        tmp.write_text(json.dumps(seen))
        os.replace(tmp, SEEN)


def open_tunnel(target_host: str) -> socket.socket:
    """Open a raw TCP tunnel to target_host:443 through the env HTTP proxy
    (CONNECT). Returns the connected socket *before* TLS: websockets/asyncio
    wraps it itself when given sock= with a wss:// URI. The websockets
    library's own proxy support hangs against this proxy, while a manual
    CONNECT works (verified 2026-11-02). Without a proxy env var, connects
    directly."""
    proxy_url = (os.environ.get("https_proxy") or os.environ.get("HTTPS_PROXY")
                 or os.environ.get("http_proxy") or os.environ.get("HTTP_PROXY"))
    if not proxy_url:
        return socket.create_connection((target_host, 443), timeout=15)
    p = urllib.parse.urlparse(proxy_url)
    s = socket.create_connection((p.hostname, p.port or 3128), timeout=15)
    req = (f"CONNECT {target_host}:443 HTTP/1.1\r\n"
           f"Host: {target_host}:443\r\n")
    if p.username:
        cred = base64.b64encode(
            f"{urllib.parse.unquote(p.username)}:{urllib.parse.unquote(p.password or '')}"
            .encode()).decode()
        req += f"Proxy-Authorization: Basic {cred}\r\n"
    req += "\r\n"
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        chunk = s.recv(4096)
        if not chunk:
            raise RuntimeError("proxy closed during CONNECT")
        resp += chunk
    status = resp.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 200 " not in status:
        raise RuntimeError(f"CONNECT failed: {status}")
    return s


async def run_session() -> None:
    """One connection lifetime. Returns when the connection drops."""
    token = load_token()
    session_id = None
    seq = None
    resume_url = None

    url = GATEWAY
    while True:
        host = urllib.parse.urlparse(url).hostname or "gateway.discord.gg"
        raw = await asyncio.to_thread(open_tunnel, host)
        async with websockets.connect(url, max_size=2**22, proxy=None,
                                      sock=raw) as ws:
            hello = json.loads(await ws.recv())
            if hello.get("op") != 10:
                raise RuntimeError(f"expected HELLO, got op {hello.get('op')}")
            interval = hello["d"]["heartbeat_interval"] / 1000.0
            log(f"HELLO heartbeat_interval={interval:.1f}s url={url}")

            stop = asyncio.Event()

            async def heartbeat() -> None:
                # First beat after jitter*interval per docs, then every beat
                # also touches the heartbeat file for the hook's liveness check.
                await asyncio.sleep(interval * 0.1)
                while not stop.is_set():
                    try:
                        await ws.send(json.dumps({"op": 1, "d": seq}))
                    except Exception:
                        return
                    HEARTBEAT.write_text(str(time.time()))
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=interval)
                    except asyncio.TimeoutError:
                        pass

            hb = asyncio.create_task(heartbeat())
            try:
                if session_id and resume_url and seq is not None:
                    await ws.send(json.dumps({
                        "op": 6,
                        "d": {"token": token, "session_id": session_id, "seq": seq},
                    }))
                    log(f"RESUME sent session={session_id} seq={seq}")
                else:
                    await ws.send(json.dumps({
                        "op": 2,
                        "d": {
                            "token": token,
                            "intents": INTENTS,
                            "properties": {
                                "os": "linux",
                                "browser": "custom.discord",
                                "device": "custom.discord",
                            },
                        },
                    }))
                    log("IDENTIFY sent")

                async for raw in ws:
                    msg = json.loads(raw)
                    op = msg.get("op")
                    if msg.get("s") is not None:
                        seq = msg["s"]
                    if op == 0:
                        t = msg.get("t")
                        d = msg.get("d") or {}
                        if t == "READY":
                            session_id = d.get("session_id")
                            resume_url = d.get("resume_gateway_url")
                            user = (d.get("user") or {}).get("username")
                            BOOT.write_text(str(time.time()))
                            log(f"READY user={user} session={session_id}")
                        elif t == "RESUMED":
                            log("RESUMED ok")
                        elif t == "MESSAGE_CREATE":
                            ch = d.get("channel_id")
                            if d.get("guild_id") == GUILD_ID and ch in WATCHED:
                                update_seen(ch, d["id"])
                                author = d.get("author") or {}
                                if author.get("id") != BOT_ID:
                                    append_inbox({
                                        "id": d["id"],
                                        "channel_id": ch,
                                        "channel": WATCHED[ch],
                                        "author": author.get("global_name")
                                                  or author.get("username"),
                                        "content": d.get("content", ""),
                                        "timestamp": d.get("timestamp"),
                                    })
                        elif t == "MESSAGE_REACTION_ADD":
                            # Reactions are mail too (mailbox design,
                            # 2026-11-15): Lothrop's ✅ in #needs-lothrop
                            # is how bus flags get resolved.
                            ch = d.get("channel_id")
                            if d.get("guild_id") == GUILD_ID and ch in WATCHED \
                                    and d.get("user_id") != BOT_ID:
                                emoji = (d.get("emoji") or {}).get("name", "")
                                member = d.get("member") or {}
                                user = member.get("user") or {}
                                append_inbox({
                                    "kind": "reaction",
                                    "id": f"{d['message_id']}:{d.get('user_id')}:{emoji}",
                                    "channel_id": ch,
                                    "channel": WATCHED[ch],
                                    "author": user.get("global_name")
                                              or user.get("username")
                                              or str(d.get("user_id")),
                                    "user_id": d.get("user_id"),
                                    "content": f"reacted {emoji} to message {d['message_id']}",
                                    "timestamp": time.strftime(
                                        "%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()),
                                })
                    elif op == 7:
                        log("RECONNECT requested by gateway; reconnecting")
                        return
                    elif op == 9:
                        log(f"INVALID_SESSION resumable={msg.get('d')}")
                        if not msg.get("d"):
                            session_id, seq, resume_url = None, None, None
                        await asyncio.sleep(2)
                        return
                    elif op == 11:
                        pass  # heartbeat ACK
            finally:
                stop.set()
                hb.cancel()
        # Connection closed cleanly: resume if we can.
        url = resume_url or GATEWAY
        log("connection closed; will reconnect")


async def main() -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    backoff = 2
    while True:
        try:
            await run_session()
            backoff = 2
        except Exception as e:  # noqa: BLE001 — daemon must never die silently
            log(f"session error: {type(e).__name__}: {e}; retry in {backoff}s")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


if __name__ == "__main__":
    asyncio.run(main())
