#!/usr/bin/env python3
"""hearth grow: the continuous self-improvement loop. hearth works on hearth.

Each cycle the box (1) recalls what it has learned from past runs, (2) asks the
model to propose ONE small, safe, concrete improvement to its own codebase or
config, (3) runs the self-evolve flow to implement that improvement on a fresh
branch and prove it passes `nix flake check`, and (4) records the outcome as a
lesson so the next cycle is better informed. Then it picks the next improvement
and repeats.

The loop produces validated branches for a human to review and merge; it NEVER
auto-merges or switches the live system, so the worst case of a bad idea is a
branch that does not build, which changes nothing. The model proposes and
implements; the loop, the nix gate, and memory are deterministic. Model / evolve
/ memory seams are injectable for testing. Standard library only.
"""

import argparse
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_state  # noqa: E402
import hearth_loop  # noqa: E402
import hearth_tools  # noqa: E402
import hearth_memory  # noqa: E402
import hearth_evolve  # noqa: E402

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MAX_CYCLES = 25
# A short pause between cycles so a fast-failing loop cannot spin the CPU; the
# real work (a full evolve) dominates, so this is just a floor.
CYCLE_PAUSE_S = 2

IDEA_SYS = (
    "You are hearth's self-improvement planner. hearth is a local NixOS box that "
    "runs local LLM agents (Python standard-library modules under agent/, a stdlib "
    "cockpit server under webui/, and NixOS modules under nixos/). Propose ONE "
    "small, safe, concrete improvement to hearth's OWN codebase or config that a "
    "single agent can implement in a few edits and that will still pass "
    "`nix flake check`. Good ideas: a small new read-only agent tool, a clearer "
    "doc comment, a minor config option with a safe default, a small refactor, a "
    "self-test assertion. AVOID anything touching networking, the bootloader, SSH, "
    "secrets, or large rewrites. Do NOT repeat an improvement that was already "
    "attempted. Reply with ONE LINE: the improvement phrased as an imperative goal "
    "(for example 'Add a uptime field to the system_health tool'). No prose, no "
    "numbering, no quotes.")


def _clean_idea(text):
    """Reduce a model reply to a single one-line imperative goal. Strips only a
    leading list marker (a bullet, or 'N.'/'N)') so a goal like '3D rendering'
    keeps its digit."""
    for raw in (text or "").splitlines():
        line = re.sub(r"^\s*(?:[-*•]|\d+[.)])\s+", "", raw.strip())
        line = line.strip().strip('"').strip("'").strip()
        if len(line) >= 6:
            return line[:200]
    return ""


def propose_idea(model, ollama_url, lessons_ctx, attempted, chat_fn=None):
    """Ask the model for the next improvement, informed by past lessons and the
    ideas already tried this run. Returns a one-line goal, or '' if none."""
    chat_fn = chat_fn or (lambda msgs: hearth_loop.chat(
        ollama_url, model, msgs, None))
    tried = "\n".join("- " + a for a in attempted) or "(none yet)"
    user = ("{}\n\nAlready attempted this run (do NOT repeat any of these):\n{}\n\n"
            "Propose the next improvement now.").format(lessons_ctx or "(no lessons yet)", tried)
    msg, _ = chat_fn([{"role": "system", "content": IDEA_SYS},
                      {"role": "user", "content": user}])
    content = msg.get("content", "") if isinstance(msg, dict) else (msg or "")
    return _clean_idea(content)


def run_growth(model, db=DEFAULT_DB, agent_id="grow", ollama_url=DEFAULT_OLLAMA,
               repo=None, max_cycles=MAX_CYCLES, pause_s=CYCLE_PAUSE_S,
               propose_fn=None, evolve_fn=None, recall_fn=None, remember_fn=None,
               emit_fn=None, sleep_fn=None):
    """Run the continuous self-improvement loop for up to max_cycles cycles.

    Returns a summary string. Each cycle recalls lessons, proposes one
    improvement, runs evolve to implement + validate it, and records the outcome.
    """
    repo = repo or hearth_tools.HEARTH_REPO
    if emit_fn is None:
        emit_fn, _ = hearth_loop.make_db_transport(db, agent_id)
    recall_fn = recall_fn or (lambda q: hearth_memory.recall(db, q, limit=6))
    remember_fn = remember_fn or (
        lambda insight, kind: hearth_memory.remember(
            db, insight, kind=kind, topic="self-improvement", source="grow"))
    propose_fn = propose_fn or (
        lambda lessons_ctx, attempted: propose_idea(
            model, ollama_url, lessons_ctx, attempted))
    evolve_fn = evolve_fn or (
        lambda goal, child_id: hearth_evolve.run_evolve(
            goal, model, db=db, agent_id=child_id, ollama_url=ollama_url, repo=repo))
    sleep_fn = sleep_fn or time.sleep

    def state(s, d):
        try:
            hearth_state.emit_state(agent_id, s, d, db=db)
        except Exception:  # noqa: BLE001
            pass
        emit_fn({"type": "state", "state": s, "detail": d})

    def say(text):
        emit_fn({"type": "message", "role": "grow", "content": text})

    attempted = []
    validated = 0
    failed = 0
    try:
        hearth_state.record_meta(agent_id, None, "growth", "continuous self-improvement", db=db)
        state("SPAWNING", "growth loop starting ({} cycles max)".format(max_cycles))
        for cyc in range(1, max_cycles + 1):
            state("THINKING", "cycle {}/{}: choosing an improvement".format(cyc, max_cycles))
            lessons = recall_fn("hearth self-improvement nix")
            idea = propose_fn(hearth_memory.as_context(lessons), attempted[-8:])
            if not idea:
                say("cycle {}: no improvement proposed; skipping".format(cyc))
                remember_fn("growth cycle {}: model proposed no idea".format(cyc), "failure")
                if pause_s:
                    sleep_fn(pause_s)
                continue
            attempted.append(idea)
            say("cycle {}: {}".format(cyc, idea))
            child_id = "{}-c{}".format(agent_id, cyc)
            state("TOOL_CALL", "cycle {}: implementing + validating".format(cyc))
            result = evolve_fn(idea, child_id)
            if result:
                validated += 1
                say("cycle {} VALIDATED: {} (branch ready for review)".format(cyc, idea))
                remember_fn(
                    "SUCCESS: improvement '{}' implemented and passed nix flake check "
                    "(validated branch from {})".format(idea, child_id), "success")
            else:
                failed += 1
                say("cycle {} failed validation: {}".format(cyc, idea))
                remember_fn(
                    "FAILED: improvement '{}' did not pass nix flake check; refine or "
                    "avoid this approach".format(idea), "failure")
            if pause_s:
                sleep_fn(pause_s)
        summary = ("growth loop finished {} cycles: {} validated improvements (branches "
                   "ready for review), {} failed validation".format(max_cycles, validated, failed))
        emit_fn({"type": "done", "final": summary, "error": None})
        state("DONE", "{} validated / {} failed".format(validated, failed))
        remember_fn(summary, "lesson")
        return summary
    except Exception as exc:  # noqa: BLE001
        err = "{}: {}".format(type(exc).__name__, exc)
        emit_fn({"type": "done", "final": None, "error": err})
        state("ERRORED", err[:200])
        return "growth loop errored: " + err


def _self_test():
    import tempfile
    import sqlite3
    d = tempfile.mkdtemp(prefix="grow-")
    db = os.path.join(d, "a.db")
    hearth_state.ensure_schema(db)
    sqlite3.connect(db).executescript(hearth_loop.TRANSCRIPT_SCHEMA)

    # _clean_idea strips list markers, quotes, and blank lines.
    assert _clean_idea("1. Add a uptime field\nextra") == "Add a uptime field"
    assert _clean_idea('  "Refactor the parser"  ') == "Refactor the parser"
    assert _clean_idea("- improve docs") == "improve docs"
    assert _clean_idea("3D rendering refactor") == "3D rendering refactor", "leading digit kept"
    assert _clean_idea("\n\nok") == "", "too-short replies rejected"

    # propose_idea pulls a one-line goal out of an injected chat fn and feeds it
    # the lessons + attempted context.
    seen = {}

    def chat(msgs):
        seen["user"] = msgs[-1]["content"]
        return {"role": "assistant", "content": "2) Add a uptime field to system_health"}, 1
    idea = propose_idea("m", "url", "Relevant lessons: be careful", ["old idea"], chat_fn=chat)
    assert idea == "Add a uptime field to system_health", idea
    assert "be careful" in seen["user"] and "old idea" in seen["user"], seen

    # run_growth: drive the loop with injected seams. Cycle 2's evolve "fails"
    # (returns None) to exercise both branches.
    ideas = ["improvement one", "improvement two", "improvement three"]
    proposed = []
    evolved = []
    lessons_seen = []
    remembered = []

    def propose(lessons_ctx, attempted):
        lessons_seen.append(lessons_ctx)
        return ideas[len(proposed) % len(ideas)] if proposed.append(1) or True else ""

    def evolve(goal, child_id):
        evolved.append((goal, child_id))
        return None if "two" in goal else "self-evolve SUCCESS: branch ready"

    def recall(q):
        return [{"kind": "lesson", "insight": "prior lesson about nix", "topic": "", "tags": ""}]

    def remember(insight, kind):
        remembered.append((kind, insight))
        return 1

    events = []
    msg = run_growth("m", db=db, agent_id="grow", repo=d, max_cycles=3, pause_s=0,
                     propose_fn=propose, evolve_fn=evolve, recall_fn=recall,
                     remember_fn=remember, emit_fn=events.append, sleep_fn=lambda s: None)
    assert "3 cycles" in msg and "2 validated" in msg and "1 failed" in msg, msg
    assert len(evolved) == 3, ("evolve runs once per cycle", evolved)
    assert evolved[0][1] == "grow-c1" and evolved[2][1] == "grow-c3", evolved
    # the recalled lessons reach the proposer as context
    assert any("prior lesson about nix" in ls for ls in lessons_seen), lessons_seen
    # outcomes recorded: 2 success + 1 failure + 1 final summary lesson
    kinds = [k for k, _ in remembered]
    assert kinds.count("success") == 2 and kinds.count("failure") == 1, remembered
    assert any("growth loop finished" in ins for _, ins in remembered), remembered
    # meta + final state landed in the db
    metas = {m["agent_id"]: m for m in hearth_state.read_meta(db)}
    assert metas["grow"]["kind"] == "growth", metas
    con = sqlite3.connect(db)
    s = con.execute("SELECT state FROM agent_state WHERE agent_id='grow'").fetchone()
    con.close()
    assert s and s[0] == "DONE", s
    assert any(e.get("type") == "done" and e.get("final") for e in events), events

    # empty-proposal path: a cycle that proposes nothing records a failure and
    # does not call evolve.
    ev2 = []
    rem2 = []
    run_growth("m", db=db, agent_id="grow2", repo=d, max_cycles=1, pause_s=0,
               propose_fn=lambda lc, at: "", evolve_fn=lambda g, c: ev2.append(g),
               recall_fn=lambda q: [], remember_fn=lambda i, k: rem2.append((k, i)),
               emit_fn=lambda e: None, sleep_fn=lambda s: None)
    assert ev2 == [], "no idea -> no evolve"
    assert any(k == "failure" for k, _ in rem2), rem2

    print("hearth-grow self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-grow")
    p.add_argument("--model", default="qwen2.5-coder")
    p.add_argument("--agent-name", default="grow")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    p.add_argument("--repo", default=None)
    p.add_argument("--max-cycles", type=int, default=MAX_CYCLES)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    summary = run_growth(a.model, db=a.db, agent_id=a.agent_name,
                         ollama_url=a.ollama_url, repo=a.repo, max_cycles=a.max_cycles)
    print(summary)
    return 0


if __name__ == "__main__":
    sys.exit(main())
