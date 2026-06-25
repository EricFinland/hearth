#!/usr/bin/env python3
"""hearth swarm: the manager that turns one goal into a coordinated team.

A manager decomposes a goal into independent subtasks (one model call), spawns a
specialist agent per subtask through the existing queue -> hearth-spawn ->
hearth-agent@ path, waits for them to finish, collects their results, and
synthesizes a final answer (one model call). The model only PLANS and
SYNTHESIZES; the orchestration (spawn, wait, collect) is deterministic code, so
the engine is unit-testable with an injected chat_fn and spawn_fn (no Ollama, no
real subprocesses). Standard library only.

Usage:
  hearth-swarm --agent-name mgr-ab12 --model qwen2.5-coder --db DB "GOAL"
  hearth-swarm --self-test
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_state  # noqa: E402
import hearth_loop  # noqa: E402  (reused for make_db_transport + TRANSCRIPT_SCHEMA)

DEFAULT_QUEUE = "/var/lib/hearth/queue"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
MAX_SUBTASKS = 5

DECOMPOSE_SYS = (
    "You are a planning manager. Break the user's goal into 2 to 5 INDEPENDENT "
    "subtasks that a specialist agent can each do alone. Reply with ONLY a JSON "
    "array of objects, each {\"name\": short label, \"prompt\": full instruction "
    "for that specialist}. No prose, no code fences.")
SYNTH_SYS = (
    "You are a manager synthesizing your team's results into one clear answer to "
    "the original goal. Be concise and concrete.")


def _chat(ollama_url, model, messages, timeout=300):
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(ollama_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return (data.get("message") or {}).get("content", "")


def parse_subtasks(text, goal):
    """Extract [{name, prompt}] from the model's decomposition. Falls back to a
    single subtask (the whole goal) if parsing fails."""
    arr = None
    try:
        arr = json.loads(text)
    except (ValueError, TypeError):
        m = re.search(r"\[.*\]", text or "", re.S)
        if m:
            try:
                arr = json.loads(m.group(0))
            except ValueError:
                arr = None
    tasks = []
    if isinstance(arr, list):
        for item in arr[:MAX_SUBTASKS]:
            if isinstance(item, dict) and item.get("prompt"):
                tasks.append({"name": str(item.get("name") or "task")[:40],
                              "prompt": str(item["prompt"])})
    if not tasks:
        tasks = [{"name": "main", "prompt": goal}]
    return tasks


def decompose(goal, model, ollama_url, chat_fn=None):
    chat_fn = chat_fn or (lambda msgs: _chat(ollama_url, model, msgs))
    text = chat_fn([{"role": "system", "content": DECOMPOSE_SYS},
                    {"role": "user", "content": goal}])
    return parse_subtasks(text, goal)


def synthesize(goal, results, model, ollama_url, chat_fn=None):
    chat_fn = chat_fn or (lambda msgs: _chat(ollama_url, model, msgs))
    body = "GOAL: {}\n\n".format(goal) + "\n\n".join(
        "SUBTASK: {}\nRESULT:\n{}".format(n, r) for n, r in results)
    return chat_fn([{"role": "system", "content": SYNTH_SYS},
                    {"role": "user", "content": body + "\n\nSynthesize the final answer."}])


def _spawn_child(childid, name, model, prompt, mode, queue_dir):
    """Drop a queue file so hearth-spawn starts a normal specialist worker."""
    os.makedirs(queue_dir, exist_ok=True)
    tmp = os.path.join(queue_dir, childid + ".json.tmp")
    final = os.path.join(queue_dir, childid + ".json")
    with open(tmp, "w") as fh:
        json.dump({"name": name, "model": model, "prompt": prompt, "mode": mode}, fh)
    os.replace(tmp, final)


def _child_state(db, childid):
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(hearth_state.SCHEMA)
            row = con.execute("SELECT state FROM agent_state WHERE agent_id=?",
                              (childid,)).fetchone()
            return row[0] if row else None
        finally:
            con.close()
    except sqlite3.Error:
        return None


def _child_final(db, childid):
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(hearth_loop.TRANSCRIPT_SCHEMA)
            rows = con.execute(
                "SELECT event FROM agent_transcript WHERE agent_id=? ORDER BY id DESC LIMIT 30",
                (childid,)).fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return "(no result)"
    for (ev,) in rows:
        try:
            e = json.loads(ev)
        except ValueError:
            continue
        if e.get("type") == "message" and e.get("content"):
            return e["content"]
        if e.get("type") in ("done", "turn_done") and e.get("final"):
            return e["final"]
    return "(no result)"


def collect(childids, db, timeout=900, poll=2.0, sleep_fn=time.sleep, clock=time.monotonic):
    """Block until every child reaches DONE/ERRORED (or timeout), returning
    {childid: final_text}."""
    deadline = clock() + timeout
    pending = list(childids)
    results = {}
    while pending and clock() < deadline:
        still = []
        for cid in pending:
            if _child_state(db, cid) in ("DONE", "ERRORED"):
                results[cid] = _child_final(db, cid)
            else:
                still.append(cid)
        pending = still
        if pending:
            sleep_fn(poll)
    for cid in pending:
        results[cid] = "(timed out)"
    return results


def run_manager(goal, model, workspace, db=DEFAULT_DB, agent_id="manager", mode="bypass",
                ollama_url=DEFAULT_OLLAMA, queue_dir=DEFAULT_QUEUE, chat_fn=None,
                spawn_fn=None, emit_fn=None, collect_kwargs=None):
    """Decompose, spawn specialists, collect, synthesize. Returns the final text."""
    spawn_fn = spawn_fn or _spawn_child
    if emit_fn is None:
        emit_fn, _ = hearth_loop.make_db_transport(db, agent_id)
    collect_kwargs = collect_kwargs or {}
    os.makedirs(workspace, exist_ok=True)

    def state(s, detail):
        try:
            hearth_state.emit_state(agent_id, s, detail, db=db)
        except Exception:  # noqa: BLE001
            pass
        emit_fn({"type": "state", "state": s, "detail": detail})

    hearth_state.record_meta(agent_id, None, "manager", goal, db=db)
    try:
        state("THINKING", "decomposing the goal")
        tasks = decompose(goal, model, ollama_url, chat_fn)
        emit_fn({"type": "message", "role": "manager",
                 "content": "decomposed into {} subtasks: {}".format(
                     len(tasks), ", ".join(t["name"] for t in tasks))})
        children = []
        for i, t in enumerate(tasks):
            cid = "{}-s{}".format(agent_id, i + 1)
            hearth_state.record_meta(cid, agent_id, "specialist", t["prompt"], db=db)
            spawn_fn(cid, t["name"], model, t["prompt"], mode, queue_dir)
            emit_fn({"type": "spawn", "child": cid, "name": t["name"]})
            children.append((cid, t["name"]))
        state("WAITING_IO", "{} specialists running".format(len(children)))
        collected = collect([c[0] for c in children], db, **collect_kwargs)
        results = [(name, collected.get(cid, "(no result)")) for cid, name in children]
        state("THINKING", "synthesizing results")
        final = synthesize(goal, results, model, ollama_url, chat_fn)
        emit_fn({"type": "message", "role": "manager", "content": final})
        emit_fn({"type": "done", "final": final, "error": None})
        state("DONE", "mission complete")
        return final
    except Exception as exc:  # noqa: BLE001 - a failed mission must still end cleanly
        err = "{}: {}".format(type(exc).__name__, exc)
        emit_fn({"type": "done", "final": None, "error": err})
        state("ERRORED", err[:200])
        return None


def _self_test():
    import tempfile
    # parse_subtasks: well-formed + fallback
    ts = parse_subtasks('[{"name":"a","prompt":"do a"},{"name":"b","prompt":"do b"}]', "g")
    assert len(ts) == 2 and ts[0]["prompt"] == "do a", ts
    assert parse_subtasks("not json at all", "the goal")[0]["prompt"] == "the goal"
    # also tolerate code-fenced / surrounded JSON via the regex fallback
    fenced = "sure:\n```json\n[{\"name\":\"z\",\"prompt\":\"pz\"}]\n```"
    assert parse_subtasks(fenced, "g")[0]["prompt"] == "pz", parse_subtasks(fenced, "g")

    # collect: pre-seed two DONE children with transcripts
    d = tempfile.mkdtemp(prefix="swarm-")
    db = os.path.join(d, "a.db")
    hearth_state.ensure_schema(db)
    con = sqlite3.connect(db)
    con.executescript(hearth_loop.TRANSCRIPT_SCHEMA)
    for cid, res in [("m-s1", "res one"), ("m-s2", "res two")]:
        con.execute("INSERT INTO agent_state (agent_id, state, detail, updated_at) VALUES (?,?,?,?)",
                    (cid, "DONE", "", hearth_state.now_iso()))
        con.execute("INSERT INTO agent_transcript (agent_id, ts, event) VALUES (?,?,?)",
                    (cid, hearth_state.now_iso(), json.dumps({"type": "message", "content": res})))
    con.commit()
    con.close()
    got = collect(["m-s1", "m-s2"], db, timeout=1, poll=0.01)
    assert got["m-s1"] == "res one" and got["m-s2"] == "res two", got

    # run_manager end-to-end: injected chat (decompose then synthesize) + a spawn_fn
    # that simulates each child finishing immediately.
    d2 = tempfile.mkdtemp(prefix="swarm2-")
    db2 = os.path.join(d2, "a.db")
    hearth_state.ensure_schema(db2)
    c0 = sqlite3.connect(db2)
    c0.executescript(hearth_loop.TRANSCRIPT_SCHEMA)
    c0.close()
    calls = []

    def fake_chat(msgs):
        calls.append(msgs)
        if len(calls) == 1:
            return '[{"name":"x","prompt":"px"},{"name":"y","prompt":"py"}]'
        return "FINAL ANSWER"

    def fake_spawn(cid, name, model, prompt, mode, queue_dir):
        c = sqlite3.connect(db2)
        c.executescript(hearth_loop.TRANSCRIPT_SCHEMA)
        c.execute("INSERT INTO agent_state (agent_id, state, detail, updated_at) VALUES (?,?,?,?)",
                  (cid, "DONE", "", hearth_state.now_iso()))
        c.execute("INSERT INTO agent_transcript (agent_id, ts, event) VALUES (?,?,?)",
                  (cid, hearth_state.now_iso(), json.dumps({"type": "message", "content": "did " + name})))
        c.commit()
        c.close()

    final = run_manager("the goal", "mock", d2, db=db2, agent_id="m", mode="bypass",
                        queue_dir=os.path.join(d2, "queue"), chat_fn=fake_chat,
                        spawn_fn=fake_spawn, collect_kwargs={"timeout": 2, "poll": 0.01})
    assert final == "FINAL ANSWER", final
    metas = {m["agent_id"]: m for m in hearth_state.read_meta(db2)}
    assert metas["m"]["kind"] == "manager", metas
    assert metas["m-s1"]["kind"] == "specialist" and metas["m-s1"]["parent_id"] == "m", metas
    print("hearth-swarm self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-swarm")
    p.add_argument("goal", nargs="?")
    p.add_argument("--model", default="qwen2.5-coder")
    p.add_argument("--agent-name", default="manager")
    p.add_argument("--workspace", default=".")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--mode", default="bypass")
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    p.add_argument("--queue-dir", default=DEFAULT_QUEUE)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.goal:
        p.error("a goal is required unless --self-test")
    final = run_manager(a.goal, a.model, a.workspace, db=a.db, agent_id=a.agent_name,
                        mode=a.mode, ollama_url=a.ollama_url, queue_dir=a.queue_dir)
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
