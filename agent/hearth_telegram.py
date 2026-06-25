#!/usr/bin/env python3
"""hearth Telegram bridge: let an agent DM the user and read replies, over the
Telegram Bot API (plain HTTP). Network I/O is injectable so it is testable with
no real bot. Standard library only.

The caller passes the bot token and chat id (resolved from the credentials
channel or env); this module is pure transport.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org/bot{token}/{method}"
HTTP_TIMEOUT = 20


def _default_post(url, payload, timeout=HTTP_TIMEOUT):
    data = urllib.parse.urlencode(payload).encode()
    req = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def _default_get(url, timeout=HTTP_TIMEOUT):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8", "replace"))


def send(token, chat_id, text, post_fn=None):
    """Send a message. Returns True on success, False on any failure (so a missing
    token or a network blip never crashes the agent)."""
    if not token or not chat_id:
        return False
    post_fn = post_fn or _default_post
    try:
        r = post_fn(API.format(token=token, method="sendMessage"),
                    {"chat_id": chat_id, "text": (text or "")[:4000]})
        return bool(r.get("ok"))
    except (urllib.error.URLError, OSError, ValueError):
        return False


def get_updates(token, offset=0, get_fn=None):
    """Return [{update_id, chat_id, text}] for messages since `offset`. Empty on
    any failure."""
    if not token:
        return []
    get_fn = get_fn or _default_get
    url = API.format(token=token, method="getUpdates")
    if offset:
        url += "?offset={}".format(offset)
    try:
        r = get_fn(url)
    except (urllib.error.URLError, OSError, ValueError):
        return []
    out = []
    for u in r.get("result", []):
        msg = u.get("message") or {}
        out.append({"update_id": u.get("update_id"),
                    "chat_id": (msg.get("chat") or {}).get("id"),
                    "text": msg.get("text", "")})
    return out


def wait_for_reply(token, chat_id, offset=0, timeout_s=600, poll=3.0,
                   get_fn=None, sleep_fn=time.sleep, clock=time.monotonic):
    """Block until the user (chat_id) sends a message, or timeout. Returns
    (text_or_None, new_offset). new_offset is the highest update_id+1 seen, so the
    caller can pass it back to avoid re-reading old messages."""
    deadline = clock() + timeout_s
    cur = offset
    while clock() < deadline:
        ups = get_updates(token, cur, get_fn=get_fn)
        for u in ups:
            if u.get("update_id") is not None and u["update_id"] >= cur:
                cur = u["update_id"] + 1
            if str(u.get("chat_id")) == str(chat_id) and u.get("text"):
                return u["text"], cur
        sleep_fn(poll)
    return None, cur


def _self_test():
    # send: a fake post that records the call and returns ok
    sent = {}
    def fake_post(url, payload, timeout=HTTP_TIMEOUT):
        sent["url"] = url
        sent["payload"] = payload
        return {"ok": True}
    assert send("TOK", "42", "hello world", post_fn=fake_post) is True
    assert "sendMessage" in sent["url"] and sent["payload"]["chat_id"] == "42"
    assert sent["payload"]["text"] == "hello world"
    assert send("", "42", "x", post_fn=fake_post) is False, "no token -> False"

    # get_updates: a fake get returning two messages
    def fake_get(url, timeout=HTTP_TIMEOUT):
        return {"ok": True, "result": [
            {"update_id": 5, "message": {"text": "hi", "chat": {"id": 42}}},
            {"update_id": 6, "message": {"text": "go", "chat": {"id": 42}}}]}
    ups = get_updates("TOK", 0, get_fn=fake_get)
    assert len(ups) == 2 and ups[1]["text"] == "go" and ups[0]["chat_id"] == 42, ups

    # wait_for_reply: returns the first matching message + advances the offset
    text, new_off = wait_for_reply("TOK", "42", offset=0, timeout_s=1, poll=0.01,
                                   get_fn=fake_get, sleep_fn=lambda s: None)
    assert text == "hi" and new_off == 6, (text, new_off)

    # wait_for_reply timeout when no matching chat
    def empty_get(url, timeout=HTTP_TIMEOUT):
        return {"ok": True, "result": []}
    t2, off2 = wait_for_reply("TOK", "42", offset=0, timeout_s=0.05, poll=0.01,
                              get_fn=empty_get, sleep_fn=lambda s: None)
    assert t2 is None, t2

    print("hearth-telegram self-test OK")
    return 0


def main(argv=None):
    import argparse
    p = argparse.ArgumentParser(prog="hearth-telegram")
    p.add_argument("--self-test", action="store_true")
    p.add_argument("--send")
    p.add_argument("--token", default="")
    p.add_argument("--chat", default="")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.send:
        ok = send(a.token, a.chat, a.send)
        print("sent" if ok else "failed")
        return 0 if ok else 1
    p.error("nothing to do (use --self-test or --send)")


if __name__ == "__main__":
    import sys
    sys.exit(main())
