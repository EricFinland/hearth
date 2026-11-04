#!/usr/bin/env python3
r"""Announce a merged pull request, or a published release, in Discord.

    python scripts/discord_announce.py pr --dry-run
    python scripts/discord_announce.py release --dry-run

THE FACTS COME FROM THE ENVIRONMENT, NOT THE COMMAND LINE
---------------------------------------------------------
A workflow that interpolated a pull request title into a shell command would
hand anyone who can open a pull request a shell on the runner: that title is
attacker-controlled text. So the workflow passes the event through `env:` and
this script reads it from there, where it stays data and never becomes code.
See .github/workflows/discord-announce.yml.

WHERE IT GOES
-------------
DISCORD_WEBHOOK_URL, the same value the agent's alert fan-out uses, validated
by the same code (agent/hearth_notify._discord_webhooks): https only, a real
Discord host, an /api/webhooks/ path. Comma separate it to announce in more
than one channel. Unset, this prints what it would have sent and exits
cleanly, so a fork, or a repository whose secret is not set, is not a failed
build.

Best-effort, like the fan-out it borrows from: an announcement that cannot be
delivered must never fail the release it is announcing. Standard library only.
"""

import argparse
import json
import os
import re
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "agent"))

import hearth_notify as hn  # noqa: E402

# Discord's own limits. It rejects an embed whose parts run past them, so
# clipping here is correctness rather than tidiness.
EMBED_TITLE = 256
FIELD_VALUE = 1024
SUMMARY_CHARS = 700  # well inside the 4096 allowed: a channel wants the lead


def _clip(text, limit):
    """Trim to `limit` characters on a word boundary, marking what was cut."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    space = cut.rfind(" ")
    if space > limit // 2:
        cut = cut[:space]
    return cut.rstrip() + "…"


def _lead(body, limit=SUMMARY_CHARS):
    """The lead of a pull request body: everything before its first markdown
    heading, with HTML comments and blank runs collapsed. A body is written
    for reviewers and can run for pages; the channel wants the part that says
    what changed."""
    body = re.sub(r"<!--.*?-->", "", body or "", flags=re.S)
    lead = re.split(r"(?m)^[ \t]*#{1,6}[ \t]+", body)[0]
    lead = re.sub(r"\n{3,}", "\n\n", lead).strip()
    return _clip(lead or "No description given.", limit)


def _edition(repo):
    """Name and colour for the edition a repository builds, so a post looks
    like it belongs to the channel it lands in."""
    if repo.endswith("-windows"):
        return "Hearth for Windows", 0x4A9EFF
    return "Hearth for NixOS", 0xCC785C


def _get(env, name):
    return (env.get(name) or "").strip()


def pr_embed(env=None):
    """Build the embed for a merged pull request from the event environment."""
    env = os.environ if env is None else env
    repo = _get(env, "GITHUB_REPOSITORY")
    edition, color = _edition(repo)
    number, title = _get(env, "PR_NUMBER"), _get(env, "PR_TITLE")
    heading = "#{} {}".format(number, title) if number else (title or "Untitled pull request")
    # The workflow only fires on a merged pull request, so PR_MERGED is true
    # there. Running this by hand against an open one must not claim a merge
    # that has not happened, so the state is read rather than assumed.
    base = _get(env, "PR_BASE") or "main"
    merged = _get(env, "PR_MERGED").lower() == "true"
    embed = {
        "author": {"name": ("Merged into {}" if merged else "Open against {}").format(base)},
        "title": _clip(heading, EMBED_TITLE),
        "description": _lead(_get(env, "PR_BODY")),
        "color": color,
        "fields": [],
        "footer": {"text": edition},
    }
    url = _get(env, "PR_URL")
    if url:
        embed["url"] = url
    if repo:
        embed["fields"].append({"name": "Repository", "value": _clip(repo, FIELD_VALUE),
                                "inline": True})
    author = _get(env, "PR_AUTHOR")
    if author:
        embed["fields"].append({"name": "By", "value": _clip(author, FIELD_VALUE),
                                "inline": True})
    return embed


def release_embed(env=None):
    """Build the embed for a published release from the event environment."""
    env = os.environ if env is None else env
    repo = _get(env, "GITHUB_REPOSITORY")
    edition, color = _edition(repo)
    tag, name = _get(env, "RELEASE_TAG"), _get(env, "RELEASE_NAME")
    heading = "{} {}".format(tag, name).strip() or "New release"
    embed = {
        "author": {"name": "New release"},
        "title": _clip(heading, EMBED_TITLE),
        "description": _lead(_get(env, "RELEASE_BODY")),
        "color": color,
        "fields": [],
        "footer": {"text": edition},
    }
    url = _get(env, "RELEASE_URL")
    if url:
        embed["url"] = url
    if repo:
        embed["fields"].append({"name": "Repository", "value": _clip(repo, FIELD_VALUE),
                                "inline": True})
    if tag:
        embed["fields"].append({"name": "Tag", "value": _clip(tag, FIELD_VALUE),
                                "inline": True})
    return embed


def post(embed, hooks=None, post_fn=None):
    """Deliver one embed to every configured channel. Returns how many took
    it. Never raises: one dead channel must not cost the others the post, and
    none of them may cost the build."""
    if hooks is None:
        try:
            hooks = hn._discord_webhooks()
        except Exception:  # noqa: BLE001 - announcing must never break the build
            hooks = []
    payload = json.dumps({"embeds": [embed]}).encode("utf-8")
    sent = 0
    for hook in hooks:
        try:
            (post_fn or hn._default_post)(hook, payload,
                                          {"Content-Type": "application/json",
                                           "User-Agent": hn.DISCORD_UA})
            sent += 1
        except Exception:  # noqa: BLE001 - announcing must never break the build
            pass
    return sent


def _self_test():
    # _clip: under the limit is untouched, over it loses whole words only
    assert _clip("short", 50) == "short"
    assert _clip("  padded  ", 50) == "padded"
    out = _clip("alpha beta gamma delta", 12)
    assert out.endswith("…") and len(out) <= 12, out
    assert not out[:-1].endswith(" "), out
    # a single word longer than the limit still fits inside it
    assert len(_clip("x" * 40, 10)) == 10

    # _lead: the part before the first heading, comments and blank runs gone
    body = "The lead.\n\n\n\nStill lead.\n\n## Why\n\nNot the lead.\n"
    assert _lead(body) == "The lead.\n\nStill lead.", repr(_lead(body))
    assert _lead("<!-- hidden -->Visible.") == "Visible."
    assert _lead("") == "No description given."
    assert _lead("## Straight to a heading") == "No description given."
    # a C preprocessor line is not a markdown heading
    assert _lead("#define X 1") == "#define X 1"

    # _edition: colour and name follow the repository
    assert _edition("EricFinland/hearth-windows") == ("Hearth for Windows", 0x4A9EFF)
    assert _edition("EricFinland/hearth") == ("Hearth for NixOS", 0xCC785C)

    # pr_embed: reads the event out of the environment it is handed
    env = {"GITHUB_REPOSITORY": "EricFinland/hearth-windows", "PR_NUMBER": "10",
           "PR_TITLE": "Discord alerts", "PR_URL": "https://github.com/x/y/pull/10",
           "PR_AUTHOR": "ericfinland", "PR_BASE": "main", "PR_MERGED": "true",
           "PR_BODY": "Adds a channel.\n\n## Testing\n\nself-test"}
    e = pr_embed(env)
    assert e["title"] == "#10 Discord alerts", e["title"]
    assert e["url"] == "https://github.com/x/y/pull/10"
    assert e["description"] == "Adds a channel.", e["description"]
    assert e["color"] == 0x4A9EFF
    assert e["footer"] == {"text": "Hearth for Windows"}
    assert e["author"] == {"name": "Merged into main"}
    assert [f["name"] for f in e["fields"]] == ["Repository", "By"], e["fields"]

    # an open pull request is never described as merged, however it is run
    for state in ("false", "", "False", "yes", "1"):
        openish = pr_embed(dict(env, PR_MERGED=state))
        assert openish["author"] == {"name": "Open against main"}, (state, openish)
    # only a plain true, in any case, counts as merged
    for state in ("true", "TRUE", "True"):
        assert pr_embed(dict(env, PR_MERGED=state))["author"] == {"name": "Merged into main"}
    # the base branch is carried through, and defaults when absent
    assert pr_embed({"PR_BASE": "release", "PR_MERGED": "true"})["author"] == {
        "name": "Merged into release"}

    # an empty environment still produces a postable embed, never a crash
    bare = pr_embed({})
    assert bare["title"] == "Untitled pull request", bare["title"]
    assert "url" not in bare and bare["fields"] == []
    assert bare["footer"] == {"text": "Hearth for NixOS"}

    # an over-long title is clipped to what Discord accepts
    assert len(pr_embed({"PR_TITLE": "word " * 200})["title"]) <= EMBED_TITLE

    # release_embed: tag and name make the heading
    r = release_embed({"GITHUB_REPOSITORY": "EricFinland/hearth", "RELEASE_TAG": "v1.8.0",
                       "RELEASE_NAME": "Hearthstone", "RELEASE_BODY": "Notes.",
                       "RELEASE_URL": "https://example.invalid/r"})
    assert r["title"] == "v1.8.0 Hearthstone", r["title"]
    assert r["color"] == 0xCC785C
    assert [f["name"] for f in r["fields"]] == ["Repository", "Tag"], r["fields"]

    # post: one call per channel, right headers, embed wrapped for the API
    calls = []
    hooks = ["https://discord.com/api/webhooks/1/a", "https://discord.com/api/webhooks/2/b"]
    assert post({"title": "t"}, hooks=hooks,
                post_fn=lambda u, d, h: calls.append((u, d, h))) == 2
    assert [c[0] for c in calls] == hooks, calls
    assert calls[0][2] == {"Content-Type": "application/json",
                           "User-Agent": hn.DISCORD_UA}, calls[0][2]
    assert json.loads(calls[0][1].decode()) == {"embeds": [{"title": "t"}]}

    # no channel configured, or a channel that throws: nothing sent, no raise
    assert post({"title": "t"}, hooks=[]) == 0

    def boom(url, data, headers):
        raise RuntimeError("network down")
    assert post({"title": "t"}, hooks=hooks, post_fn=boom) == 0

    # one channel failing leaves the other one's post alone
    def flaky(url, data, headers):
        if url.endswith("/a"):
            raise RuntimeError("gone")
    assert post({"title": "t"}, hooks=hooks, post_fn=flaky) == 1

    # a value that is not a Discord webhook never becomes a channel: the
    # validator is agent/hearth_notify's, not a second copy of it here
    saved = os.environ.pop("DISCORD_WEBHOOK_URL", None)
    try:
        os.environ["DISCORD_WEBHOOK_URL"] = "https://evil.example/api/webhooks/1/a"
        assert post({"title": "t"}, post_fn=lambda u, d, h: calls.append(u)) == 0
    finally:
        if saved is None:
            os.environ.pop("DISCORD_WEBHOOK_URL", None)
        else:
            os.environ["DISCORD_WEBHOOK_URL"] = saved

    print("discord-announce self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="discord-announce",
                                description=__doc__.splitlines()[0])
    p.add_argument("event", nargs="?", choices=("pr", "release"),
                   help="which event to announce; its facts come from the environment")
    p.add_argument("--dry-run", action="store_true",
                   help="print the payload instead of posting it")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.event:
        p.error("nothing to do (use --self-test, or name an event)")
    embed = pr_embed() if a.event == "pr" else release_embed()
    if a.dry_run:
        print(json.dumps({"embeds": [embed]}, indent=2))
        return 0
    hooks = hn._discord_webhooks()
    if not hooks:
        # No channel here (a fork, or a repository whose secret is unset).
        # Say so and succeed: a missing announcement is not a broken build.
        print("no Discord channel configured, nothing announced")
        print(json.dumps({"embeds": [embed]}, indent=2))
        return 0
    n = post(embed, hooks=hooks)
    print("announced in {} of {} channel(s)".format(n, len(hooks)))
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
