#!/usr/bin/env python3
"""hearth notify: unified alert fan-out.

One call, `notify(kind, text)`, delivers an operator alert to every channel
that is configured, and stays silent when none are. Channels:

  Telegram  token/chat from the credentials channel (telegram_token /
            telegram_chat via hearth_tools._resolve_cred) with env fallback
            (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID), sent via hearth_telegram.
  ntfy      plain-text POST to {HEARTH_NTFY_URL or https://ntfy.sh}/{topic}
            when HEARTH_NTFY_TOPIC is set.
  Discord   JSON POST to one or more channel webhooks, taken from the
            credentials channel (discord_webhook) with env fallback
            (DISCORD_WEBHOOK_URL), comma separated for more than one
            channel. Only a real discord.com /api/webhooks/ endpoint is
            accepted, so a typo cannot quietly hand operator alerts to a
            stranger.

Kinds in use: done, error, tripwire, budget. Network I/O is injectable
(post_fn) so it is testable with no real services. Best-effort throughout:
a notification failure must never break the caller. Standard library only.
"""

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_telegram  # noqa: E402
import hearth_tools  # noqa: E402

DEFAULT_NTFY_URL = "https://ntfy.sh"
HTTP_TIMEOUT = 10
DISCORD_HOSTS = ("discord.com", "discordapp.com", "ptb.discord.com",
                 "canary.discord.com")
DISCORD_LIMIT = 2000  # Discord rejects a message body longer than this
# Discord requires every API client to name itself, and its edge answers a
# request carrying urllib's default agent with Cloudflare error 1010, so the
# channel does not work at all without this header.
DISCORD_UA = "DiscordBot (https://github.com/EricFinland/hearth, 0.1)"


def _telegram_creds():
    """Resolve the Telegram token and chat id the same way the growth daemon
    does: named credentials first, env fallback. Empty strings when unset."""
    token = hearth_tools._resolve_cred("telegram_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = hearth_tools._resolve_cred("telegram_chat") or os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat


def _discord_webhooks():
    """Resolve the Discord channel webhooks: named credential first, env
    fallback, comma separated for more than one channel (the two editions
    keep a release channel each). A value that is not a Discord webhook
    endpoint is dropped rather than used, so a misconfigured or swapped-in
    URL leaves that channel dormant instead of posting operator alerts
    somewhere else. Returns a list, deduplicated, order preserved."""
    raw = (hearth_tools._resolve_cred("discord_webhook")
           or os.environ.get("DISCORD_WEBHOOK_URL", ""))
    hooks = []
    for url in raw.split(","):
        url = url.strip()
        if not url:
            continue
        parts = urllib.parse.urlsplit(url)
        if parts.scheme != "https" or parts.hostname not in DISCORD_HOSTS:
            continue
        if not parts.path.startswith("/api/webhooks/"):
            continue
        if url not in hooks:
            hooks.append(url)
    return hooks


def _default_post(url, data, headers):
    """POST raw bytes with headers. Raises on failure (urllib error status)."""
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
        resp.read()


def notify(kind, text, post_fn=None):
    """Send "hearth {kind}: {text}" to every configured channel. Returns the
    number of channels that accepted the message. Never raises: an unreachable
    or misconfigured channel just does not count."""
    line = "hearth {}: {}".format(kind, text)
    sent = 0
    # Telegram (dormant until a token + chat are configured)
    try:
        token, chat = _telegram_creds()
        if token and chat and hearth_telegram.send(token, chat, line):
            sent += 1
    except Exception:  # noqa: BLE001 - alerting must never break the caller
        pass
    # ntfy (dormant until a topic is configured)
    try:
        topic = os.environ.get("HEARTH_NTFY_TOPIC", "")
        if topic:
            base = os.environ.get("HEARTH_NTFY_URL", "") or DEFAULT_NTFY_URL
            url = base.rstrip("/") + "/" + topic
            (post_fn or _default_post)(url, line.encode("utf-8"),
                                       {"Title": "hearth {}".format(kind)})
            sent += 1
    except Exception:  # noqa: BLE001 - alerting must never break the caller
        pass
    # Discord (dormant until a channel webhook is configured). One bad or
    # unreachable channel must not cost the others their alert, so each is
    # attempted on its own.
    try:
        hooks = _discord_webhooks()
    except Exception:  # noqa: BLE001 - alerting must never break the caller
        hooks = []
    body = json.dumps({"content": line[:DISCORD_LIMIT]}).encode("utf-8")
    for hook in hooks:
        try:
            (post_fn or _default_post)(hook, body,
                                       {"Content-Type": "application/json",
                                        "User-Agent": DISCORD_UA})
            sent += 1
        except Exception:  # noqa: BLE001 - alerting must never break the caller
            pass
    return sent


def _self_test():
    saved = {}
    for k in ("HEARTH_NTFY_TOPIC", "HEARTH_NTFY_URL", "TELEGRAM_BOT_TOKEN",
              "TELEGRAM_CHAT_ID", "DISCORD_WEBHOOK_URL", "CREDENTIALS_DIRECTORY"):
        saved[k] = os.environ.pop(k, None)
    try:
        calls = []

        def spy(url, data, headers):
            calls.append((url, data, headers))

        # no channel configured: nothing sent, no network touched
        assert notify("done", "x", post_fn=spy) == 0, "no channels -> 0"
        assert calls == [], calls

        # ntfy: builds the right url, title header, and plain-text body
        os.environ["HEARTH_NTFY_TOPIC"] = "alerts"
        n = notify("budget", "agent b paused", post_fn=spy)
        assert n == 1, n
        url, data, headers = calls[-1]
        assert url == "https://ntfy.sh/alerts", url
        assert headers == {"Title": "hearth budget"}, headers
        assert data == b"hearth budget: agent b paused", data

        # custom server, trailing slash trimmed
        os.environ["HEARTH_NTFY_URL"] = "https://ntfy.example.com/"
        notify("tripwire", "t", post_fn=spy)
        assert calls[-1][0] == "https://ntfy.example.com/alerts", calls[-1]
        assert calls[-1][2] == {"Title": "hearth tripwire"}, calls[-1]

        # discord: a real webhook gets the line as JSON
        os.environ.pop("HEARTH_NTFY_TOPIC")
        os.environ["DISCORD_WEBHOOK_URL"] = "https://discord.com/api/webhooks/1/abc"
        assert notify("error", "agent b crashed", post_fn=spy) == 1, "discord -> 1"
        url, data, headers = calls[-1]
        assert url == "https://discord.com/api/webhooks/1/abc", url
        assert headers == {"Content-Type": "application/json",
                           "User-Agent": DISCORD_UA}, headers
        assert json.loads(data.decode())["content"] == "hearth error: agent b crashed", data

        # a long line is clipped to what Discord will accept
        notify("done", "x" * 4000, post_fn=spy)
        assert len(json.loads(calls[-1][1].decode())["content"]) == DISCORD_LIMIT

        # two channels: both get the line, and a bad entry listed alongside
        # them is dropped without taking the good ones down with it
        os.environ["DISCORD_WEBHOOK_URL"] = (
            "https://discord.com/api/webhooks/1/abc , https://discord.com/api/webhooks/2/def,"
            "https://evil.example/api/webhooks/3/ghi")
        before = len(calls)
        assert notify("done", "two", post_fn=spy) == 2, "two webhooks -> 2"
        assert [c[0] for c in calls[before:]] == ["https://discord.com/api/webhooks/1/abc",
                                                 "https://discord.com/api/webhooks/2/def"], calls[before:]

        # the same channel listed twice is still one alert
        os.environ["DISCORD_WEBHOOK_URL"] = ("https://discord.com/api/webhooks/1/abc,"
                                             "https://discord.com/api/webhooks/1/abc")
        assert notify("done", "dup", post_fn=spy) == 1, "deduplicated -> 1"

        # one channel failing leaves the other one's alert alone
        os.environ["DISCORD_WEBHOOK_URL"] = ("https://discord.com/api/webhooks/1/abc,"
                                             "https://discord.com/api/webhooks/2/def")

        def flaky(url, data, headers):
            if url.endswith("/abc"):
                raise RuntimeError("channel gone")
            calls.append((url, data, headers))
        before = len(calls)
        assert notify("done", "partial", post_fn=flaky) == 1, "one of two -> 1"
        assert [c[0] for c in calls[before:]] == ["https://discord.com/api/webhooks/2/def"], calls[before:]

        # not a Discord webhook endpoint: dormant, and no request made at all
        for bad in ("http://discord.com/api/webhooks/1/a",
                    "https://evil.example/api/webhooks/1/a",
                    "https://discord.com/channels/1/2",
                    "https://notdiscord.com/api/webhooks/1/a"):
            os.environ["DISCORD_WEBHOOK_URL"] = bad
            before = len(calls)
            assert notify("done", "y", post_fn=spy) == 0, bad
            assert len(calls) == before, bad
        os.environ.pop("DISCORD_WEBHOOK_URL")
        os.environ["HEARTH_NTFY_TOPIC"] = "alerts"

        # a raising post_fn is swallowed and simply not counted
        def boom(url, data, headers):
            raise RuntimeError("network down")
        assert notify("error", "x", post_fn=boom) == 0, "raising channel -> 0, no raise"

        # fan-out: with Telegram creds set too, both channels count
        os.environ["TELEGRAM_BOT_TOKEN"] = "T"
        os.environ["TELEGRAM_CHAT_ID"] = "42"
        tg = []
        real_send = hearth_telegram.send
        hearth_telegram.send = lambda token, chat, text: tg.append((token, chat, text)) or True
        try:
            n2 = notify("done", "finished", post_fn=spy)
        finally:
            hearth_telegram.send = real_send
        assert n2 == 2, n2
        assert tg == [("T", "42", "hearth done: finished")], tg
    finally:
        for k, v in saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    print("hearth-notify self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-notify")
    p.add_argument("--send", help="text to send to every configured channel")
    p.add_argument("--kind", default="done")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.send:
        n = notify(a.kind, a.send)
        print("sent to {} channel(s)".format(n))
        return 0 if n else 1
    p.error("nothing to do (use --self-test or --send)")


if __name__ == "__main__":
    sys.exit(main())
