#!/usr/bin/env python3
"""hearth self-evolve: the box proposes a change to its OWN NixOS config and
proves it builds locally before any human touches it.

Flow: make a fresh git branch in hearth's config repo, let the model edit the
flake (read_self_config / write_self_config), then run `nix flake check` locally
as the gate. On failure the error is fed back and the model fixes it, looping
until the flake evaluates clean or a round cap. On success it commits the branch
and reports it ready for review + merge. It NEVER switches the live system: the
worst case is a branch that does not build, which changes nothing.

The model only edits; git + the nix gate are deterministic. Model/nix/git/Telegram
seams are injectable for testing. Standard library only.
"""

import argparse
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_state  # noqa: E402
import hearth_loop  # noqa: E402
import hearth_tools  # noqa: E402
import hearth_telegram  # noqa: E402

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MAX_ROUNDS = 8

EVOLVE_SYS = (
    "You are improving hearth's OWN NixOS configuration (a flake) to achieve a "
    "change. Use read_self_config to read files and write_self_config to edit them "
    "(paths are relative to the config repo root). Keep edits MINIMAL and valid "
    "Nix. Do NOT touch networking, the bootloader, or SSH. After your edits an "
    "automated `nix flake check` runs; if it fails you will receive the error to fix.")


def _git(repo, *args, timeout=180):
    git = hearth_tools._bin("git", "/run/current-system/sw/bin/git")
    return subprocess.run([git, "-C", repo, *args], capture_output=True, text=True, timeout=timeout)


def run_evolve(goal, model, db=DEFAULT_DB, agent_id="evolve", ollama_url=DEFAULT_OLLAMA,
               repo=None, max_rounds=MAX_ROUNDS, branch=None, turn_chat_fn=None,
               nix_check_fn=None, git_fn=None, emit_fn=None, tg_send=None,
               tg_token=None, tg_chat=None):
    """Propose + locally-validate a self-change on a branch. Returns a success
    message, or None if it could not reach a valid config."""
    repo = repo or hearth_tools.HEARTH_REPO
    branch = branch or "hearth-evolve-{}".format(agent_id)
    git_fn = git_fn or (lambda *a: _git(repo, *a))
    nix_check_fn = nix_check_fn or (lambda: hearth_tools.execute_tool("nix_check", {}, repo))
    if emit_fn is None:
        emit_fn, _ = hearth_loop.make_db_transport(db, agent_id)
    turn_chat_fn = turn_chat_fn or (lambda msgs: hearth_loop.chat(
        ollama_url, model, msgs, hearth_tools.ollama_tool_specs()))
    if tg_token is None:
        tg_token = hearth_tools._resolve_cred("telegram_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    if tg_chat is None:
        tg_chat = hearth_tools._resolve_cred("telegram_chat") or os.environ.get("TELEGRAM_CHAT_ID", "")
    tg_send = tg_send or hearth_telegram.send

    # Point the self-config tools at this repo for the duration of the run.
    os.environ["HEARTH_REPO"] = repo
    hearth_tools.HEARTH_REPO = repo

    def state(s, d):
        try:
            hearth_state.emit_state(agent_id, s, d, db=db)
        except Exception:  # noqa: BLE001
            pass
        emit_fn({"type": "state", "state": s, "detail": d})

    def dm(text):
        if tg_token and tg_chat:
            try:
                tg_send(tg_token, tg_chat, text)
            except Exception:  # noqa: BLE001
                pass

    hearth_state.record_meta(agent_id, None, "evolve", goal, db=db)
    state("SPAWNING", "self-evolve: " + goal[:120])
    try:
        # Bootstrap: ensure the repo is a git repo with a baseline on main.
        if not os.path.isdir(os.path.join(repo, ".git")):
            git_fn("init", "-b", "main")
            git_fn("add", "-A")
            git_fn("-c", "user.name=hearth", "-c", "user.email=hearth@local",
                   "commit", "-m", "baseline")
        git_fn("checkout", "-B", branch)

        messages = [{"role": "system", "content": EVOLVE_SYS},
                    {"role": "user", "content": "Change to make: " + goal}]
        st = {"mode": "bypass"}
        last_err = ""
        ok = False
        for rnd in range(1, max_rounds + 1):
            state("THINKING", "round {}: editing".format(rnd))
            hearth_loop._run_turns(messages, model, repo, turn_chat_fn, emit_fn,
                                   hearth_loop._stdin_control, st, db, agent_id,
                                   hearth_loop.MAX_ITERS, ())
            state("TOOL_CALL", "nix flake check (round {})".format(rnd))
            res = nix_check_fn()
            emit_fn({"type": "tool_result", "tool": "nix_check", "output": (res or "")[:1500]})
            if isinstance(res, str) and res.strip().startswith("nix_check PASS"):
                ok = True
                break
            last_err = (res or "")[:1500]
            messages.append({"role": "user",
                             "content": "nix flake check FAILED. Fix the config so it evaluates. Error:\n" + last_err})

        if ok:
            git_fn("add", "-A")
            git_fn("-c", "user.name=hearth", "-c", "user.email=hearth@local",
                   "commit", "-m", "hearth self-evolve: " + goal[:72])
            msg = ("self-evolve SUCCESS: branch '{}' passes nix flake check. "
                   "Review with: git -C {} diff main..{} ; merge to main and nixos-rebuild to deploy."
                   ).format(branch, repo, branch)
            emit_fn({"type": "done", "final": msg, "error": None})
            state("DONE", "validated branch " + branch)
            dm("hearth " + msg)
            return msg

        msg = ("self-evolve could not reach a valid config in {} rounds; the branch '{}' is "
               "left for inspection. Last nix error:\n{}").format(max_rounds, branch, last_err)
        emit_fn({"type": "done", "final": None, "error": msg[:400]})
        state("ERRORED", "nix check never passed")
        dm("hearth " + msg[:400])
        return None
    except Exception as exc:  # noqa: BLE001
        err = "{}: {}".format(type(exc).__name__, exc)
        emit_fn({"type": "done", "final": None, "error": err})
        state("ERRORED", err[:200])
        dm("hearth self-evolve error: " + err[:200])
        return None


def _self_test():
    import tempfile
    import sqlite3
    d = tempfile.mkdtemp(prefix="evolve-")
    db = os.path.join(d, "a.db")
    hearth_state.ensure_schema(db)
    sqlite3.connect(db).executescript(hearth_loop.TRANSCRIPT_SCHEMA)

    git_calls = []
    checks = []
    sent = []

    def turn_chat(msgs):
        return {"role": "assistant", "content": "edited the flake"}, 1

    def nixc():
        checks.append(1)
        return "nix_check PASS\nall good" if len(checks) >= 2 else "nix_check FAIL\nerror: bad attribute"

    class _R:
        returncode = 0
        stdout = ""
        stderr = ""

    def gitf(*a):
        git_calls.append(a)
        return _R()

    def tg(token, chat, text):
        sent.append(text)
        return True

    # make the repo dir look like an existing git repo so bootstrap is skipped
    os.makedirs(os.path.join(d, ".git"))
    msg = run_evolve("add a comment to a module", "mock", db=db, agent_id="ev", repo=d,
                     max_rounds=5, turn_chat_fn=turn_chat, nix_check_fn=nixc, git_fn=gitf,
                     tg_send=tg, tg_token="T", tg_chat="1")
    assert msg and "SUCCESS" in msg, msg
    assert len(checks) == 2, ("failed once then passed", checks)
    assert any(a[0] == "checkout" for a in git_calls), git_calls
    assert any(a and a[-2] == "commit" or "commit" in a for a in git_calls), git_calls
    metas = {m["agent_id"]: m for m in hearth_state.read_meta(db)}
    assert metas["ev"]["kind"] == "evolve", metas
    assert any("SUCCESS" in s for s in sent), sent

    # failure path: nix never passes -> returns None, branch left, ERRORED
    checks2 = []

    def nixc_fail():
        checks2.append(1)
        return "nix_check FAIL\nstill broken"

    m2 = run_evolve("x", "mock", db=db, agent_id="ev2", repo=d, max_rounds=3,
                    turn_chat_fn=turn_chat, nix_check_fn=nixc_fail, git_fn=gitf,
                    tg_send=tg, tg_token="T", tg_chat="1")
    assert m2 is None, m2
    assert len(checks2) == 3, ("tried max_rounds", checks2)
    con = sqlite3.connect(db)
    s = con.execute("SELECT state FROM agent_state WHERE agent_id='ev2'").fetchone()
    con.close()
    assert s and s[0] == "ERRORED", s

    print("hearth-evolve self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-evolve")
    p.add_argument("goal", nargs="?")
    p.add_argument("--model", default="qwen2.5-coder")
    p.add_argument("--agent-name", default="evolve")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    p.add_argument("--repo", default=None)
    p.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.goal:
        p.error("a goal is required unless --self-test")
    msg = run_evolve(a.goal, a.model, db=a.db, agent_id=a.agent_name,
                     ollama_url=a.ollama_url, repo=a.repo, max_rounds=a.max_rounds)
    print(msg or "self-evolve did not produce a valid branch")
    return 0 if msg else 1


if __name__ == "__main__":
    sys.exit(main())
