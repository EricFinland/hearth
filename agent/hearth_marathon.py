#!/usr/bin/env python3
"""hearth marathon: loop-until-done. Point it at a goal and it works in rounds
until the goal is actually complete (or you stop it), instead of stopping at a
fixed iteration cap. After each round it DMs progress to Telegram and, in
check-in mode, waits for you to reply keep-going / stop / steer.

The model PLANS each round of work and JUDGES completion; the round mechanics,
Telegram I/O, and looping are deterministic and injectable for testing.
Standard library only.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_state  # noqa: E402
import hearth_loop  # noqa: E402
import hearth_telegram  # noqa: E402
import hearth_tools  # noqa: E402

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MAX_ROUNDS = 30
CHECKIN_TIMEOUT_S = 1800  # 30 min to reply on Telegram before auto-continuing

JUDGE_SYS = (
    "You judge whether a work goal is FULLY complete, based on the recent work log. "
    "Reply with exactly DONE if the goal is fully achieved. Otherwise reply "
    "CONTINUE: followed by the single most important next step. No other text.")
_STOP_WORDS = {"stop", "halt", "cancel", "no", "abort"}
_GO_WORDS = {"go", "continue", "yes", "keep going", "y", ""}


def _chat(ollama_url, model, messages, timeout=300):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return (json.loads(resp.read().decode()).get("message") or {}).get("content", "")


# Output/artifact file extensions: when a goal names a file like these, it is a
# deliverable we can deterministically verify exists before believing "DONE".
# Source-ish extensions (.py/.nix/.sh) are intentionally excluded — they are
# often inputs the agent only reads, so requiring them would cause false vetoes.
_ARTIFACT_EXT = ("png", "ppm", "pgm", "jpg", "jpeg", "gif", "svg", "webp", "mp4",
                 "mov", "mkv", "webm", "mp3", "wav", "flac", "md", "txt", "csv",
                 "tsv", "html", "pdf", "json", "yaml", "yml")
_ARTIFACT_RE = re.compile(r"[\w./-]+\.(?:" + "|".join(_ARTIFACT_EXT) + r")\b", re.I)


def required_artifacts(goal):
    """Artifact filenames the goal names as deliverables (de-duped)."""
    out = []
    for m in _ARTIFACT_RE.finditer(goal or ""):
        name = m.group(0)
        if name not in out:
            out.append(name)
    return out


def missing_artifacts(goal, workspace, size_fn=None):
    """Of the deliverables named in the goal, those missing or empty in the
    workspace. Empty list means every named artifact exists and is non-empty."""
    def _size(p):
        try:
            return os.path.getsize(p)
        except OSError:
            return -1
    size_fn = size_fn or _size
    missing = []
    for name in required_artifacts(goal):
        base = os.path.basename(name)
        cands = [os.path.join(workspace, name), os.path.join(workspace, base)]
        if not any(size_fn(c) > 0 for c in cands):
            missing.append(base)
    return missing


def judge(goal, worklog, model, ollama_url, judge_fn=None):
    """Return (done_bool, next_step_str)."""
    judge_fn = judge_fn or (lambda msgs: _chat(ollama_url, model, msgs))
    text = (judge_fn([{"role": "system", "content": JUDGE_SYS},
                      {"role": "user", "content": "GOAL: {}\n\nRECENT WORK LOG:\n{}".format(goal, worklog)}]) or "").strip()
    if text.upper().startswith("DONE"):
        return True, ""
    i = text.upper().find("CONTINUE:")
    return False, (text[i + 9:].strip() if i >= 0 else text)


def _resolve_telegram():
    token = hearth_tools._resolve_cred("telegram_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = hearth_tools._resolve_cred("telegram_chat") or os.environ.get("TELEGRAM_CHAT_ID", "")
    return token, chat


def run_marathon(goal, model, workspace, db=DEFAULT_DB, agent_id="marathon", mode="bypass",
                 ollama_url=DEFAULT_OLLAMA, max_rounds=MAX_ROUNDS, checkin=False,
                 tg_token=None, tg_chat=None, turn_chat_fn=None, judge_fn=None,
                 emit_fn=None, tg_send=None, tg_wait=None, round_sleep=0.0,
                 verify_artifacts=True, artifact_size_fn=None):
    """Work in rounds until the goal is judged DONE, the user says stop, or
    max_rounds. Returns the final text (or None on error)."""
    if tg_token is None or tg_chat is None:
        rt, rc = _resolve_telegram()
        tg_token = tg_token if tg_token is not None else rt
        tg_chat = tg_chat if tg_chat is not None else rc
    tg_send = tg_send or hearth_telegram.send
    tg_wait = tg_wait or hearth_telegram.wait_for_reply
    if emit_fn is None:
        emit_fn, _ = hearth_loop.make_db_transport(db, agent_id)
    turn_chat_fn = turn_chat_fn or (lambda msgs: hearth_loop.chat(
        ollama_url, model, msgs, hearth_tools.ollama_tool_specs()))
    os.makedirs(workspace, exist_ok=True)

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

    hearth_state.record_meta(agent_id, None, "marathon", goal, db=db)
    state("SPAWNING", "marathon starting")
    dm("hearth marathon started:\n" + goal[:300])
    messages = [{"role": "system", "content": hearth_loop.SYSTEM_PROMPT},
                {"role": "user", "content": goal}]
    st = {"mode": mode}
    tg_offset = 0
    final = ""
    rounds = 0
    try:
        for rounds in range(1, max_rounds + 1):
            state("THINKING", "round {}".format(rounds))
            f, err, _ = hearth_loop._run_turns(messages, model, workspace, turn_chat_fn,
                                               emit_fn, hearth_loop._stdin_control, st, db,
                                               agent_id, hearth_loop.MAX_ITERS, ())
            final = f or final
            worklog = "\n".join(m.get("content", "") for m in messages[-6:] if m.get("content"))[:2500]
            done, nxt = judge(goal, worklog, model, ollama_url, judge_fn)
            # Don't take the model's word for it: if the goal names deliverable
            # files that are missing or empty, the goal is NOT done. Veto and tell
            # the model exactly what to produce. (Fixes credulous "done" claims.)
            if done and verify_artifacts:
                miss = missing_artifacts(goal, workspace, artifact_size_fn)
                if miss:
                    done = False
                    nxt = ("not done yet: the goal requires these file(s) which are "
                           "missing or empty: " + ", ".join(miss) + ". Produce them, then finish.")
                    emit_fn({"type": "message", "role": "marathon",
                             "content": "round {} completion vetoed: missing {}".format(rounds, ", ".join(miss))})
            dm("round {}: {}".format(rounds, "COMPLETE" if done else ("next: " + nxt)[:300]))
            emit_fn({"type": "message", "role": "marathon",
                     "content": "round {} {}".format(rounds, "complete" if done else "-> " + nxt)})
            if done:
                break
            if checkin and tg_token and tg_chat:
                state("WAITING_APPROVAL", "awaiting your Telegram reply")
                reply, tg_offset = tg_wait(tg_token, tg_chat, tg_offset, CHECKIN_TIMEOUT_S)
                low = (reply or "").strip().lower()
                if low in _STOP_WORDS:
                    final = final or "stopped by user"
                    emit_fn({"type": "done", "final": final, "error": None})
                    state("DONE", "stopped via Telegram")
                    dm("hearth marathon stopped at round {}.".format(rounds))
                    return final
                if reply and low not in _GO_WORDS:
                    messages.append({"role": "user", "content": "User steering: " + reply})
            messages.append({"role": "user", "content":
                             "Continue toward the goal. Next step: " + (nxt or "keep going until fully done.")})
            if round_sleep:
                time.sleep(round_sleep)
        dm("hearth marathon finished after {} rounds.".format(rounds))
        emit_fn({"type": "done", "final": final, "error": None})
        state("DONE", "marathon complete ({} rounds)".format(rounds))
        return final
    except Exception as exc:  # noqa: BLE001 - a marathon must end cleanly
        msg = "{}: {}".format(type(exc).__name__, exc)
        dm("hearth marathon error: " + msg[:300])
        emit_fn({"type": "done", "final": None, "error": msg})
        state("ERRORED", msg[:200])
        return None


def _self_test():
    import tempfile
    import sqlite3
    d = tempfile.mkdtemp(prefix="marathon-")
    db = os.path.join(d, "a.db")
    hearth_state.ensure_schema(db)
    sqlite3.connect(db).executescript(hearth_loop.TRANSCRIPT_SCHEMA)

    work = []
    judges = []
    sent = []

    def turn_chat(msgs):
        work.append(1)
        return {"role": "assistant", "content": "did some work"}, 1

    def judge_fn(msgs):
        judges.append(1)
        return "DONE" if len(judges) >= 2 else "CONTINUE: do part two"

    def tg_send(tok, chat, text):
        sent.append(text)
        return True

    def tg_wait(tok, chat, off, timeout):
        return "go", off + 1

    final = run_marathon("build the thing", "mock", d, db=db, agent_id="mar", mode="bypass",
                         max_rounds=10, checkin=True, tg_token="TOK", tg_chat="42",
                         turn_chat_fn=turn_chat, judge_fn=judge_fn, tg_send=tg_send, tg_wait=tg_wait)
    assert len(judges) == 2, ("looped until DONE on round 2", judges)
    assert len(work) >= 2, work
    assert any("started" in s for s in sent) and any("finished" in s for s in sent), sent
    metas = {m["agent_id"]: m for m in hearth_state.read_meta(db)}
    assert metas["mar"]["kind"] == "marathon", metas

    # stop path: judge never done, user replies "stop" -> ends after round 1
    sent2 = []

    def judge_never(msgs):
        return "CONTINUE: keep going forever"

    def tg_wait_stop(tok, chat, off, timeout):
        return "stop", off + 1

    run_marathon("x", "mock", d, db=db, agent_id="mar2", mode="bypass", max_rounds=10,
                 checkin=True, tg_token="T", tg_chat="1", turn_chat_fn=turn_chat,
                 judge_fn=judge_never, tg_send=lambda *a: sent2.append(a) or True, tg_wait=tg_wait_stop)
    con = sqlite3.connect(db)
    s = con.execute("SELECT state, detail FROM agent_state WHERE agent_id='mar2'").fetchone()
    con.close()
    assert s and s[0] == "DONE" and "Telegram" in (s[1] or ""), ("stop path", s)

    # artifact extraction + missing-detection (pure)
    assert required_artifacts("save it to mandel.png and a self_portrait.md please") == \
        ["mandel.png", "self_portrait.md"], required_artifacts("save it to mandel.png and a self_portrait.md")
    assert required_artifacts("read config.py and run it") == [], "source files are not deliverables"
    dd = tempfile.mkdtemp(prefix="marathon-art-")
    assert missing_artifacts("make out.png", dd) == ["out.png"], "missing file detected"
    with open(os.path.join(dd, "out.png"), "wb") as fh:
        fh.write(b"x" * 50)
    assert missing_artifacts("make out.png", dd) == [], "present non-empty file accepted"

    # completion veto: judge always says DONE, but the marathon refuses to finish
    # until the named deliverable actually exists. The turn writes it on round 2.
    d3 = tempfile.mkdtemp(prefix="marathon-veto-")
    db3 = os.path.join(d3, "a.db")
    hearth_state.ensure_schema(db3)
    sqlite3.connect(db3).executescript(hearth_loop.TRANSCRIPT_SCHEMA)
    turns = []

    def turn_make(msgs):
        turns.append(1)
        if len(turns) >= 2:  # produce the deliverable on the second round
            with open(os.path.join(d3, "report.md"), "w") as fh:
                fh.write("the report body")
        return {"role": "assistant", "content": "working"}, 1

    run_marathon("write the briefing to report.md", "mock", d3, db=db3, agent_id="mv",
                 mode="bypass", max_rounds=8, checkin=False, tg_token="", tg_chat="",
                 turn_chat_fn=turn_make, judge_fn=lambda msgs: "DONE",
                 tg_send=lambda *a: True)
    assert len(turns) == 2, ("vetoed round 1 (no file), accepted round 2 once written", turns)
    con = sqlite3.connect(db3)
    s3 = con.execute("SELECT state FROM agent_state WHERE agent_id='mv'").fetchone()
    con.close()
    assert s3 and s3[0] == "DONE", s3

    print("hearth-marathon self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-marathon")
    p.add_argument("goal", nargs="?")
    p.add_argument("--model", default="qwen2.5-coder")
    p.add_argument("--agent-name", default="marathon")
    p.add_argument("--workspace", default=".")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--mode", default="bypass")
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    p.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    p.add_argument("--checkin", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.goal:
        p.error("a goal is required unless --self-test")
    final = run_marathon(a.goal, a.model, a.workspace, db=a.db, agent_id=a.agent_name,
                         mode=a.mode, ollama_url=a.ollama_url, max_rounds=a.max_rounds,
                         checkin=a.checkin)
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
