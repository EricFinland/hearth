# Autonomy Milestone 1, Plan 2: Swarm Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** A manager agent takes one goal, decomposes it into independent subtasks, spawns a specialist agent per subtask through the existing queue → hearth-spawn → `hearth-agent@` path, waits for them to finish, collects their results, and synthesizes a final answer. Parent → child linkage is recorded so the map (Plan 3) can draw the tree.

**Architecture:** The model only **plans** (decompose) and **synthesizes** (one call each); the orchestration (spawn, poll, collect) is deterministic Python, which makes the whole engine unit-testable without Ollama by injecting a fake `chat_fn` and a fake `spawn_fn`. Children are normal background workers (the existing `--io db` loop), spawned by the manager writing queue files; the manager polls `agent_state` for each child reaching `DONE`/`ERRORED` and reads its final from `agent_transcript`. A new `agent_meta` table records each agent's `parent_id`/`kind`/`goal`.

**Tech Stack:** Python 3 stdlib only. Tests via in-module `_self_test()` (`python <module> --self-test`). Dev machine Windows (`python`). One NixOS edit (`spawn.nix`) + a mapd `/run` field + a `hearth-loop --manager` entrypoint. Blade deploy/verify at the end. Plan 2 of 4 in Autonomy Milestone 1 (vision: `docs/superpowers/specs/2026-06-24-hearth-autonomy-vision.md`).

**Commit identity:** `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<msg>"`. No AI attribution. No em-dashes.

**Working dir:** `C:/Users/ericc/hearth-wt` (branch worktree-desktop). Blade: `ssh operator@192.168.1.64`.

---

### Task SW-1: `agent_meta` table + helpers in `agent/hearth_state.py`

**Files:** Modify `agent/hearth_state.py`

- [ ] **Step 1: Failing self-test.** `hearth_state.py` already has a `_self_test()` (added earlier). Append before its final `print(...)`:
```python
    import tempfile as _tf2
    mdb = _os.path.join(_tf2.mkdtemp(prefix="hearth-meta-"), "m.db")
    record_meta("mgr-1", None, "manager", "build a thing", db=mdb)
    record_meta("mgr-1-s1", "mgr-1", "specialist", "do part one", db=mdb)
    metas = {m["agent_id"]: m for m in read_meta(mdb)}
    assert metas["mgr-1"]["kind"] == "manager" and metas["mgr-1"]["parent_id"] is None, metas
    assert metas["mgr-1-s1"]["parent_id"] == "mgr-1", metas
```
(`_os` is already imported in the existing `_self_test`; if the existing test imports `os` differently, use whatever name is in scope.)

- [ ] **Step 2: Run `python agent/hearth_state.py --self-test`** -> expect `NameError: name 'record_meta' is not defined`.

- [ ] **Step 3: Add the table to `SCHEMA`.** In the `SCHEMA` string, add a third table:
```sql
CREATE TABLE IF NOT EXISTS agent_meta (
  agent_id   TEXT PRIMARY KEY,
  parent_id  TEXT,
  kind       TEXT,
  goal       TEXT,
  created_at TEXT NOT NULL
);
```

- [ ] **Step 4: Add the functions** (after `emit_state`):
```python
def record_meta(agent_id, parent_id, kind, goal, db=DEFAULT_DB):
    """Record an agent's lineage (parent, kind, originating goal). Upsert so a
    re-record is harmless. kind is 'manager' | 'specialist' | 'session' | 'worker'."""
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO agent_meta (agent_id, parent_id, kind, goal, created_at) "
            "VALUES (?,?,?,?,?) ON CONFLICT(agent_id) DO UPDATE SET "
            "parent_id=excluded.parent_id, kind=excluded.kind, goal=excluded.goal",
            (agent_id, parent_id, kind, (goal or "")[:2000], now_iso()))
        con.commit()
    finally:
        con.close()


def read_meta(db=DEFAULT_DB):
    """All recorded agent lineage rows (for the map to draw the tree)."""
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        cur = con.execute(
            "SELECT agent_id, parent_id, kind, goal, created_at FROM agent_meta ORDER BY created_at")
        return [{"agent_id": r[0], "parent_id": r[1], "kind": r[2], "goal": r[3] or "",
                 "created_at": r[4]} for r in cur.fetchall()]
    finally:
        con.close()
```

- [ ] **Step 5: Run `python agent/hearth_state.py --self-test`** -> `hearth-state self-test OK`.

- [ ] **Step 6: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_state.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: agent_meta lineage table (parent/kind/goal) for the swarm tree"
```

---

### Task SW-2: the swarm manager (`agent/hearth_swarm.py`)

**Files:** Create `agent/hearth_swarm.py`

- [ ] **Step 1: Create the file with the full implementation AND the self-test below.** Then run it; fix only if the self-test fails.

```python
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
```

- [ ] **Step 2: Run `python agent/hearth_swarm.py --self-test`** -> expect `hearth-swarm self-test OK`. If it fails, fix the implementation (do not weaken the asserts). Note: this imports `hearth_loop`, which imports `hearth_tools`/`hearth_state` and may print event JSON during ITS self-test only when run as main; importing it here does not run its self-test.

- [ ] **Step 3: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_swarm.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: swarm manager (decompose, spawn specialists, collect, synthesize)"
```

---

### Task SW-3: Wire it in — `--manager` entrypoint, `/run` swarm flag, spawn runner branch

**Files:** Modify `agent/hearth_loop.py`, `webui/hearth_mapd.py`, `nixos/modules/spawn.nix`

- [ ] **Step 1: `hearth-loop --manager` entrypoint (`agent/hearth_loop.py`).** In `main`, add the flag near `--session`:
```python
    p.add_argument("--manager", action="store_true",
                   help="run as a swarm manager (decompose a goal and spawn specialists)")
```
In the dispatch section, BEFORE the `if a.session:` block, add (lazy import avoids an import cycle):
```python
    if a.manager:
        import hearth_swarm  # noqa: E402 - lazy import to avoid a cycle
        if not a.goal:
            p.error("a goal is required with --manager")
        final = hearth_swarm.run_manager(a.goal, a.model, a.workspace, db=a.db,
                                         agent_id=a.agent_name, mode=a.mode,
                                         ollama_url=a.ollama_url)
        print(final)
        return 0
```
(`a.mode`, `a.ollama_url`, `a.agent_name`, `a.workspace`, `a.db` already exist as args.)

- [ ] **Step 2: Verify the loop self-test still passes.** Run `python agent/hearth_loop.py --self-test` -> `hearth-loop self-test OK`.

- [ ] **Step 3: `/run` accepts a swarm flag (`webui/hearth_mapd.py`).** In `_handle_run`, after the mode/creds parsing, add:
```python
        swarm = bool(req.get("swarm"))
```
and include it in the queued JSON:
```python
                json.dump({"name": name, "model": model, "prompt": prompt,
                           "mode": mode, "creds": allowed, "swarm": swarm}, fh)
```
Run `python webui/hearth_mapd.py --self-test` -> still OK.

- [ ] **Step 4: spawn runner branches on swarm (`nixos/modules/spawn.nix`).** In the runner `text`, read the swarm flag, and choose the entrypoint. After the existing `mode=` / `creds=` extraction add:
```nix
      swarm="$(python3 -c "import json,sys;print('1' if json.load(open(sys.argv[1])).get('swarm') else '')" "$req")"
```
Replace the final `exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$id" ... "$prompt"` line with a branch:
```nix
      if [ -n "$swarm" ]; then
        exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --manager --agent-name "$id" --model "$model" --mode "$mode" --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
      fi
      exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$id" --model "$model" --mode "$mode" --io db --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
```
(Keep the existing `[ -n "$creds" ] && export HEARTH_ALLOWED_CREDS="$creds"` line before both `exec`s. The manager does not use `--io db` because it manages its own DB writes via `hearth_swarm`.)

- [ ] **Step 5: Confirm `hearth_swarm.py` is packaged.** Read `nixos/modules/agents.nix` (or wherever `hearth.agents.loopPackage` is defined). Confirm the package copies the whole `agent/` directory (so `hearth_swarm.py` ships next to `hearth_loop.py`). If it lists files explicitly, add `hearth_swarm.py`. Report what you found. (Real eval happens on the blade in SW-4.)

- [ ] **Step 6: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_loop.py webui/hearth_mapd.py nixos/modules/spawn.nix
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: wire swarm manager (loop --manager, /run swarm flag, spawn runner branch)"
```

---

### Task SW-4: Deploy to the blade, verify a real mission, push

**Files:** none.

- [ ] **Step 1: Local gate.** Run each, expect its OK line:
```bash
python agent/permissions.py
python agent/hearth_state.py --self-test
python agent/hearth_tools.py
python agent/hearth_swarm.py --self-test
python agent/hearth_loop.py --self-test
python webui/hearth_mapd.py --self-test
```

- [ ] **Step 2: Deploy.**
```bash
cd C:/Users/ericc/hearth-wt
git archive -o C:/Users/ericc/AppData/Local/Temp/wt.tar HEAD
for i in 1 2 3 4; do scp -o ConnectTimeout=25 C:/Users/ericc/AppData/Local/Temp/wt.tar operator@192.168.1.64:~/wt.tar && break || sleep 10; done
ssh -o ConnectTimeout=30 operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -2'
```
If `hearth_swarm.py` is not packaged and the manager fails to start, fix packaging in `agents.nix` (SW-3 Step 5) and redeploy.

- [ ] **Step 3: Launch a real mission and watch the swarm work.**
```bash
ssh operator@192.168.1.64 'set +e
curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"mission\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"swarm\":true,\"prompt\":\"Produce a short report on this machine: one part on its NixOS generation history, one part on current system health.\"}"; echo
echo "waiting for the manager + specialists..."; sleep 120
echo "=== lineage (agent_meta) ==="; python3 -c "import sqlite3,json;c=sqlite3.connect(\"/var/lib/hearth/runs/audit.db\");[print(r) for r in c.execute(\"select agent_id,parent_id,kind from agent_meta order by created_at desc limit 8\")]"
echo "=== manager final ==="; python3 -c "import sqlite3,json;c=sqlite3.connect(\"/var/lib/hearth/runs/audit.db\");rows=[json.loads(r[0]) for r in c.execute(\"select event from agent_transcript where agent_id like \"+chr(39)+\"mission-%\"+chr(39)+\" and agent_id not like \"+chr(39)+\"%-s%\"+chr(39)+\" order by id\")];m=[e.get(\"content\") for e in rows if e.get(\"type\")==\"message\"];print((m[-1] if m else \"(none)\")[:500])"'
```
Expected: `agent_meta` shows one `manager` row and 2+ `specialist` rows whose `parent_id` is the manager; the manager's final message is a synthesized report that reflects the specialists' work. The specialists ran as their own `hearth-agent@` units (visible in `systemctl list-units "hearth-agent@*"` during the run).

- [ ] **Step 4: Push.**
```bash
cd C:/Users/ericc/hearth-wt && git fetch origin && git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before swarm engine" && git push origin worktree-desktop:main
```

---

## Self-Review
- Coverage: agent_meta + helpers (SW-1); the manager engine decompose/spawn/collect/synthesize with injectable seams (SW-2); wiring through loop/mapd/spawn (SW-3); a real multi-agent mission on the blade (SW-4).
- Determinism/testability: model calls isolated to `decompose`/`synthesize` (injectable `chat_fn`); spawning isolated to `spawn_fn`; polling uses injectable `sleep_fn`/`clock`. The self-test runs the full `run_manager` with no Ollama and no real subprocess.
- Placeholders: none; complete code; exact verify commands.
- Consistency: `record_meta`/`read_meta` (SW-1) used by `run_manager` and the SW-4 verify; `agent_meta(agent_id, parent_id, kind, goal, created_at)`; child ids `"{manager}-s{i}"`; children are normal `--io db` workers, the manager is `--manager` (no `--io db`); `/run` `swarm` flag flows to the queue JSON and the spawn runner branches on it. Reuses `hearth_loop.make_db_transport` (transcript emit) and `hearth_loop.TRANSCRIPT_SCHEMA`; lazy import in `hearth_loop --manager` avoids an import cycle.
- Known follow-on: the live mission depends on `hearth_swarm.py` being packaged (SW-3 Step 5); the map drawing the tree from `agent_meta` is Plan 3.
