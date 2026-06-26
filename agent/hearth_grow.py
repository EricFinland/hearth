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
import hearth_telegram  # noqa: E402

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


def merge_validated(repo, branch, nix_check_fn=None, git_fn=None):
    """Merge a validated branch into main and keep it ONLY if main still passes
    `nix flake check` afterward, so growth compounds on a known-good baseline
    (the next cycle then branches from the improved main). Any failure aborts or
    reverts so main is never left broken. Returns (merged_bool, detail)."""
    git_fn = git_fn or (lambda *a: hearth_evolve._git(repo, *a))
    nix_check_fn = nix_check_fn or (lambda: hearth_tools.execute_tool("nix_check", {}, repo))
    co = git_fn("checkout", "main")
    if co.returncode != 0:
        return False, "could not checkout main: " + ((getattr(co, "stderr", "") or "").strip()[:140] or "unknown")
    m = git_fn("-c", "user.name=hearth", "-c", "user.email=hearth@local",
               "merge", "--no-ff", "-m", "grow: merge " + branch, branch)
    if m.returncode != 0:
        git_fn("merge", "--abort")
        return False, "merge conflict; left as a branch"
    res = nix_check_fn()
    if isinstance(res, str) and res.strip().startswith("nix_check PASS"):
        return True, "merged into main"
    # The combination broke even though the branch passed alone: undo the merge.
    git_fn("reset", "--hard", "HEAD~1")
    return False, "post-merge nix check failed; reverted, left as a branch"


def _default_notifier():
    """Build a Telegram notifier from the resolved credentials, or a no-op if none
    are configured. The growth daemon stays silent until a token + chat are set
    (same credential channel evolve uses), so this is dormant by default."""
    token = hearth_tools._resolve_cred("telegram_token") or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat = hearth_tools._resolve_cred("telegram_chat") or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not (token and chat):
        return lambda text: None

    def notify(text):
        try:
            hearth_telegram.send(token, chat, text)
        except Exception:  # noqa: BLE001
            pass
    return notify


def run_growth(model, db=DEFAULT_DB, agent_id="grow", ollama_url=DEFAULT_OLLAMA,
               repo=None, max_cycles=MAX_CYCLES, pause_s=CYCLE_PAUSE_S,
               propose_fn=None, evolve_fn=None, recall_fn=None, remember_fn=None,
               emit_fn=None, sleep_fn=None, compound=True, merge_fn=None, notify_fn=None):
    """Run the continuous self-improvement loop for up to max_cycles cycles.

    Returns a summary string. Each cycle recalls lessons, proposes one
    improvement, runs evolve to implement + validate it, and records the outcome.
    When compound is set, a validated branch is merged into main (gated by a
    re-check) so the next cycle builds on it instead of a stale baseline.
    notify_fn (default: Telegram if creds are set, else no-op) receives a short
    line on each merged improvement and on the batch summary.
    """
    repo = repo or hearth_tools.HEARTH_REPO
    if compound and merge_fn is None:
        merge_fn = lambda branch: merge_validated(repo, branch)  # noqa: E731
    if notify_fn is None:
        notify_fn = _default_notifier()
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
    merged = 0
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
            # Link this evolve child to the daemon so the map/tree shows a
            # self-improvement crew (run_evolve records it parentless; re-record).
            try:
                hearth_state.record_meta(child_id, agent_id, "evolve", idea, db=db)
            except Exception:  # noqa: BLE001
                pass
            if result:
                validated += 1
                say("cycle {} VALIDATED: {} (branch ready for review)".format(cyc, idea))
                outcome = "validated branch"
                if merge_fn:
                    branch = "hearth-evolve-" + child_id
                    try:
                        ok, detail = merge_fn(branch)
                    except Exception as mexc:  # noqa: BLE001
                        ok, detail = False, "merge error: {}".format(mexc)
                    if ok:
                        merged += 1
                        outcome = "merged into main (growth compounds)"
                        say("cycle {} MERGED: {}".format(cyc, idea))
                        notify_fn("hearth merged a self-improvement: " + idea)
                    else:
                        outcome = "kept as branch ({})".format(detail)
                        say("cycle {} kept as branch: {}".format(cyc, detail))
                remember_fn(
                    "SUCCESS: improvement '{}' passed nix flake check; {} (from {})".format(
                        idea, outcome, child_id), "success")
            else:
                failed += 1
                say("cycle {} failed validation: {}".format(cyc, idea))
                remember_fn(
                    "FAILED: improvement '{}' did not pass nix flake check; refine or "
                    "avoid this approach".format(idea), "failure")
            if pause_s:
                sleep_fn(pause_s)
        summary = ("growth loop finished {} cycles: {} validated ({} merged into main), "
                   "{} failed validation".format(max_cycles, validated, merged, failed))
        emit_fn({"type": "done", "final": summary, "error": None})
        state("DONE", "{} validated / {} merged / {} failed".format(validated, merged, failed))
        remember_fn(summary, "lesson")
        # Only ping on a batch that actually did something, so an idle loop is silent.
        if validated or failed:
            notify_fn("hearth growth batch done: " + summary)
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
                     compound=False, propose_fn=propose, evolve_fn=evolve, recall_fn=recall,
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
    # evolve children are linked to the growth daemon (workshop crew in the map)
    assert metas["grow-c1"]["parent_id"] == "grow", metas["grow-c1"]
    assert metas["grow-c1"]["kind"] == "evolve", metas["grow-c1"]
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

    # merge_validated: success (merge ok + check PASS), conflict (merge nonzero),
    # and post-merge failure (check FAIL -> revert). git/nix are injected.
    class _R:
        def __init__(self, rc=0):
            self.returncode = rc
            self.stdout = ""
            self.stderr = ""
    calls = []

    def gf_ok(*a):
        calls.append(a)
        return _R(0)
    ok, _ = merge_validated("/repo", "br1", nix_check_fn=lambda: "nix_check PASS\nok", git_fn=gf_ok)
    assert ok, "merge + passing check -> merged"
    assert ("checkout", "main") in calls and any("merge" in a for a in calls), calls

    def gf_conflict(*a):
        return _R(1 if ("merge" in a and "--abort" not in a) else 0)
    okc, dc = merge_validated("/repo", "br2", nix_check_fn=lambda: "nix_check PASS", git_fn=gf_conflict)
    assert not okc and "conflict" in dc, (okc, dc)

    reverted = []

    def gf_fail(*a):
        if a[0] == "reset":
            reverted.append(a)
        return _R(0)
    okf, df = merge_validated("/repo", "br3", nix_check_fn=lambda: "nix_check FAIL\nboom", git_fn=gf_fail)
    assert not okf and reverted, ("post-merge fail must revert", okf, df, reverted)

    # run_growth with injected merge_fn + notify_fn: validated cycles are merged,
    # the branch name is derived from the child id, the summary counts merges, and
    # the notifier gets a line per merge plus the batch summary.
    merges = []
    rem3 = []
    notes = []
    msg3 = run_growth("m", db=db, agent_id="cgrow", repo=d, max_cycles=2, pause_s=0,
                      propose_fn=lambda lc, at: "idea {}".format(len(merges)),
                      evolve_fn=lambda g, c: "ok",
                      merge_fn=lambda branch: (merges.append(branch) or True, "merged into main"),
                      notify_fn=lambda text: notes.append(text),
                      recall_fn=lambda q: [], remember_fn=lambda i, k: rem3.append((k, i)),
                      emit_fn=lambda e: None, sleep_fn=lambda s: None)
    assert merges == ["hearth-evolve-cgrow-c1", "hearth-evolve-cgrow-c2"], merges
    assert "2 merged into main" in msg3, msg3
    assert sum("merged a self-improvement" in n for n in notes) == 2, notes
    assert any("growth batch done" in n for n in notes), notes

    # _default_notifier with no creds is a safe no-op callable (never crashes).
    n = _default_notifier()
    assert n("anything") is None, "no-creds notifier no-ops"

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
