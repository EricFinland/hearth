# hearth Agent Control: Background Workers + Approvals Queue (Plan 3 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give fire-and-forget background workers full-machine reach and oversight: they stream their transcript to the audit DB, default to bypass, and when launched in auto they park risky actions in a cockpit approvals queue you clear whenever. Plus a kill switch that stops everything.

**Architecture:** The agent loop (`hearth_loop.py`, from Plan 1) already takes an injectable event sink and control source. This plan adds a DB-backed transport: a background worker writes every event to an `agent_transcript` table and, on a gated tool, writes a row to `pending_actions` and polls it for a decision. `hearth-mapd` exposes `/pending`, `/decide`, and `/transcript`; the cockpit shows an approvals queue. The `hearth-agent@` systemd units are unsandboxed to run as `operator` (full reach, like mapd in Plan 2). `/stop-all` also stops running background units.

**Tech Stack:** Python 3 standard library only (sqlite3, threading, subprocess). Tests use the in-module `_self_test()` convention run via `python <module> --self-test` (no pytest). NixOS module edit for the units. Dev machine is Windows (`python`). Blade deploy/verify is deferred until the box is reachable again (its WiFi is flaky); all code is unit-testable locally without Ollama or the blade.

**Decisions (locked with the user):**
- Background workers default to **bypass** (full auto, no prompts) but can be launched in **auto**, where risky actions become pending approvals in the cockpit. (Interactive sessions from Plan 2 already do live approve/deny.)
- Background workers run unsandboxed as `operator` with full-machine reach (same posture as the mapd-hosted sessions in Plan 2).

**Scope note:** Plan 3 of 3 (spec: `docs/superpowers/specs/2026-06-22-hearth-agent-control.md`; Plans 1 and 2 are merged to main). This completes the feature.

**Commit identity (required):** every commit MUST be authored as Eric, no AI attribution. Use exactly:
`git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<message>"`
No em-dashes in any committed file or message.

**Working directory:** the worktree `C:/Users/ericc/hearth-wt` (branch `worktree-desktop`). Blade: `ssh operator@192.168.1.64`, deploy via `git archive -o file.tar HEAD` + `scp` + `sudo nixos-rebuild switch --flake ~/hearth-desktop#blade`.

---

### Task 1: DB-backed transport in the agent loop

Add a transport so a background worker (no stdio peer) persists its transcript and gates through the audit DB. Wire it behind a `--io db` flag.

**Files:**
- Modify: `agent/hearth_loop.py`

- [ ] **Step 1: Write the failing self-test**

In `agent/hearth_loop.py`, append this block to `_self_test()` just before its final `print(...)`/`return 0`:

```python
    # --- DB transport: a background worker writes its transcript and gates via the
    # audit DB. A helper thread plays the approver (sets the pending row to allow).
    import sqlite3 as _sql
    import threading as _th
    wsd = tempfile.mkdtemp(prefix="hearth-loopDB-")
    dbp = os.path.join(wsd, "audit.db")
    emit_db, control_db = make_db_transport(dbp, "bgtest", poll_interval=0.01)

    def _approver():
        for _ in range(500):
            try:
                c = _sql.connect(dbp, timeout=10)
                row = c.execute("SELECT id FROM pending_actions "
                                "WHERE agent_id='bgtest' AND decision IS NULL "
                                "ORDER BY id LIMIT 1").fetchone()
                if row:
                    c.execute("UPDATE pending_actions SET decision='allow' WHERE id=?", (row[0],))
                    c.commit()
                    c.close()
                    return
                c.close()
            except _sql.Error:
                pass
            time.sleep(0.01)

    th = _th.Thread(target=_approver, daemon=True)
    th.start()
    db_steps = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "run_command",
            "arguments": {"command": "echo dbok"}}}]}, 1),
        ({"role": "assistant", "content": "done"}, 1),
    ]
    dbseq = iter(db_steps)
    fdb, edb = run_loop("echo", "mock", wsd, db=dbp, agent_name="bgtest", mode="auto",
                        chat_fn=lambda m: next(dbseq), emit_fn=emit_db, control_fn=control_db)
    assert edb is None, edb
    th.join(timeout=2)
    con = _sql.connect(dbp, timeout=10)
    tr = [json.loads(r[0]) for r in con.execute(
        "SELECT event FROM agent_transcript WHERE agent_id='bgtest' ORDER BY id")]
    pend = con.execute("SELECT tool, decision FROM pending_actions WHERE agent_id='bgtest'").fetchall()
    con.close()
    assert any(e.get("type") == "tool_request" for e in tr), tr
    ran = [e for e in tr if e.get("type") == "tool_result" and not e.get("denied")]
    assert ran and "dbok" in ran[0].get("output", ""), ("expected the approved command to run", tr)
    assert pend and pend[0][0] == "run_command" and pend[0][1] == "allow", pend
```

- [ ] **Step 2: Run to verify it fails**

Run: `python agent/hearth_loop.py --self-test`
Expected: `NameError: name 'make_db_transport' is not defined`.

- [ ] **Step 3: Add the transcript/pending schema constant**

Near the other module constants (after `MAX_EVENT_OUT`), add:

```python
TRANSCRIPT_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_transcript (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL, req_id TEXT NOT NULL, tool TEXT, args TEXT, risk TEXT,
  created_at TEXT NOT NULL, decision TEXT
);
"""
```

- [ ] **Step 4: Add `make_db_transport`**

Add this function above `run_loop` (it uses `_now_iso`, already defined):

```python
def make_db_transport(db, agent_id, poll_interval=0.5):
    """Return (emit_fn, control_fn) for a background worker that has no stdio peer.
    emit_fn appends every event to agent_transcript, and additionally records a
    pending_actions row whenever the worker requests approval for a gated tool.
    control_fn blocks polling that row until a decision is written (by the
    /decide endpoint). The worker is stopped by stopping its systemd unit, which
    kills this process, so control_fn does not need its own stop path."""

    def _con():
        con = sqlite3.connect(db, timeout=10)
        con.executescript(TRANSCRIPT_SCHEMA)
        return con

    def emit(event):
        try:
            con = _con()
            con.execute(
                "INSERT INTO agent_transcript (agent_id, ts, event) VALUES (?,?,?)",
                (agent_id, _now_iso(), json.dumps(event)))
            if event.get("type") == "tool_request":
                con.execute(
                    "INSERT INTO pending_actions (agent_id, req_id, tool, args, risk, created_at) "
                    "VALUES (?,?,?,?,?,?)",
                    (agent_id, event.get("id"), event.get("tool"),
                     json.dumps(event.get("args") or {}), event.get("risk"), _now_iso()))
            con.commit()
            con.close()
        except sqlite3.Error:
            pass

    def control(request):
        req_id = request.get("id")
        while True:
            decision = None
            try:
                con = _con()
                row = con.execute(
                    "SELECT decision FROM pending_actions "
                    "WHERE agent_id=? AND req_id=? ORDER BY id DESC LIMIT 1",
                    (agent_id, req_id)).fetchone()
                con.close()
                decision = row[0] if row else None
            except sqlite3.Error:
                decision = None
            if decision:
                return {"type": "decision", "id": req_id, "allow": decision == "allow"}
            time.sleep(poll_interval)

    return emit, control
```

- [ ] **Step 5: Wire the `--io` flag in `main`**

In `main`, add an argument near `--mode`:
```python
    p.add_argument("--io", choices=["stdio", "db"], default="stdio",
                   help="event/control transport: stdio (interactive) or db (background worker)")
```
In the dispatch section, BEFORE the `if a.session:` check, build the transport when `--io db` is selected. Replace the line:
```python
    auto_allow = tuple(x for x in a.auto_allow.split(",") if x)
```
with:
```python
    auto_allow = tuple(x for x in a.auto_allow.split(",") if x)
    emit_fn = control_fn = None
    if a.io == "db":
        emit_fn, control_fn = make_db_transport(a.db, a.agent_name)
```
Then pass `emit_fn=emit_fn, control_fn=control_fn` into BOTH the `run_session(...)` call and the `run_loop(...)` call in `main` (when None they fall back to stdio inside those functions, so this is safe for the default path).

- [ ] **Step 6: Run to verify the self-test passes**

Run: `python agent/hearth_loop.py --self-test`
Expected: `hearth-loop self-test OK: ...`, exit 0 (all prior cases plus the DB-transport case).

- [ ] **Step 7: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_loop.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: DB-backed transport for background workers (transcript + pending approvals)"
```

---

### Task 2: mapd endpoints for transcript and approvals

Expose the background transcript and the approvals queue.

**Files:**
- Modify: `webui/hearth_mapd.py`

- [ ] **Step 1: Write the failing self-test**

In `webui/hearth_mapd.py`, append to `_self_test()` (before its final `print`):

```python
    # --- pending/transcript DB helpers: seed rows and read them back.
    import tempfile as _tf
    pdb = os.path.join(_tf.mkdtemp(prefix="hearth-mapd-pend-"), "audit.db")
    con = sqlite3.connect(pdb, timeout=10)
    con.executescript(PENDING_SCHEMA)
    con.execute("INSERT INTO agent_transcript (agent_id, ts, event) VALUES (?,?,?)",
                ("bg1", _now_iso(), json.dumps({"type": "message", "content": "hi"})))
    con.execute("INSERT INTO pending_actions (agent_id, req_id, tool, args, risk, created_at) "
                "VALUES (?,?,?,?,?,?)", ("bg1", "r1", "run_command", "{}", "dangerous", _now_iso()))
    con.commit(); con.close()
    assert read_pending(pdb) and read_pending(pdb)[0]["tool"] == "run_command", read_pending(pdb)
    assert decide_action(pdb, read_pending(pdb)[0]["id"], True) is True
    assert read_pending(pdb) == [], "decided action should leave the pending list"
    tr = read_transcript(pdb, "bg1")
    assert tr and tr[0]["event"]["type"] == "message", tr
```

- [ ] **Step 2: Run to verify it fails**

Run: `python webui/hearth_mapd.py --self-test`
Expected: `NameError` on `PENDING_SCHEMA` / `read_pending`.

- [ ] **Step 3: Add the schema and helpers**

Near the top-level helpers (after the existing `read_runs`), add:

```python
PENDING_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_transcript (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL, ts TEXT NOT NULL, event TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS pending_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL, req_id TEXT NOT NULL, tool TEXT, args TEXT, risk TEXT,
  created_at TEXT NOT NULL, decision TEXT
);
"""


def read_pending(db):
    """Undecided approval requests, oldest first."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(PENDING_SCHEMA)
        cur = con.execute(
            "SELECT id, agent_id, req_id, tool, args, risk, created_at FROM pending_actions "
            "WHERE decision IS NULL ORDER BY id")
        cols = ["id", "agent_id", "req_id", "tool", "args", "risk", "created_at"]
        rows = []
        for r in cur.fetchall():
            d = dict(zip(cols, r))
            try:
                d["args"] = json.loads(d["args"] or "{}")
            except ValueError:
                d["args"] = {}
            rows.append(d)
        con.close()
        return rows
    except sqlite3.Error:
        return []


def decide_action(db, action_id, allow):
    """Mark a pending action allow/deny so the waiting worker proceeds."""
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(PENDING_SCHEMA)
        con.execute("UPDATE pending_actions SET decision=? WHERE id=?",
                    ("allow" if allow else "deny", action_id))
        con.commit()
        con.close()
        return True
    except sqlite3.Error:
        return False


def read_transcript(db, agent_id, limit=200):
    """Transcript events for one background worker, oldest first."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(PENDING_SCHEMA)
        cur = con.execute(
            "SELECT ts, event FROM agent_transcript WHERE agent_id=? ORDER BY id LIMIT ?",
            (agent_id, limit))
        rows = []
        for ts, ev in cur.fetchall():
            try:
                rows.append({"ts": ts, "event": json.loads(ev)})
            except ValueError:
                pass
        con.close()
        return rows
    except sqlite3.Error:
        return []
```

- [ ] **Step 4: Add the routes**

In `do_GET`, before the final 404, add:
```python
        if path == "/pending":
            return self._send(200, json.dumps({"pending": read_pending(self.db)}), "application/json")
        if path == "/transcript":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            agent = (qs.get("agent") or [""])[0]
            return self._send(200, json.dumps({"transcript": read_transcript(self.db, agent)}),
                              "application/json")
```
Add `import urllib.parse` to the imports if not already present (the file imports `urllib.request`/`urllib.error`; add `import urllib.parse`).

In `do_POST`, after the `/stop-all` route, add:
```python
        if path == "/decide":
            req = self._read_json_body()
            ok = decide_action(self.db, req.get("id"), bool(req.get("allow")))
            return self._send(200, json.dumps({"ok": ok}), "application/json")
```

- [ ] **Step 5: Run to verify the self-test passes**

Run: `python webui/hearth_mapd.py --self-test`
Expected: `hearth-mapd self-test OK`, exit 0.

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/hearth_mapd.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: mapd pending-approvals + background transcript endpoints"
```

---

### Task 3: Cockpit approvals queue + background launch mode

**Files:**
- Modify: `webui/static/command.html`

- [ ] **Step 1: Add an approvals card**

Inside `#grid`, after the `#activity` card, add:
```html
  <div class="card" id="pending" style="grid-column:1/3;grid-row:5/6;display:none;">
    <div class="title">pending approvals</div>
    <div id="pendBody"></div>
  </div>
```
Adjust the grid so the approvals card has room: in the `<style>` block, change the `#grid` `grid-template-rows` value from `auto auto 1fr 180px` to `auto auto 1fr 140px auto`, and change the `#activity` rule `grid-row` from `4 / 5` to `4 / 5` (unchanged) - only the rows track list changes. (If the existing rows track differs, just append one `auto` track at the end so row 5 exists.)

- [ ] **Step 2: Add the approvals polling JS**

At the end of the `<script>` block add:
```javascript
// ---- background-worker approvals queue ----
async function refreshPending(){
  try{
    const j=await (await fetch("/pending")).json();
    const list=j.pending||[];
    const card=document.getElementById("pending");
    card.style.display=list.length?"block":"none";
    document.getElementById("pendBody").innerHTML=list.map(p=>
      `<div style="border:1px solid #6a5a1a;background:#241f0e;border-radius:6px;padding:6px;margin:4px 0;">
        <div><b style="color:#f0a030">${p.agent_id}</b> wants <b>${(p.tool||"").replace(/</g,"&lt;")}</b> <small style="color:#8a93a0">(${p.risk||""})</small></div>
        <pre style="white-space:pre-wrap;margin:4px 0;color:#cfe6ff">${JSON.stringify(p.args).replace(/</g,"&lt;")}</pre>
        <button onclick="decideAction(${p.id},true)" style="background:#1c6;color:#04150c;border:0;border-radius:6px;padding:3px 10px;cursor:pointer;">approve</button>
        <button onclick="decideAction(${p.id},false)" style="background:#a33;color:#fff;border:0;border-radius:6px;padding:3px 10px;cursor:pointer;margin-left:6px;">deny</button>
      </div>`).join("");
  }catch(e){}
}
function decideAction(id,allow){
  fetch("/decide",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({id,allow})}).then(()=>refreshPending());
}
setInterval(refreshPending,2500); refreshPending();
```
Note: `p.id` is a server integer, so the inline `onclick="decideAction(${p.id},...)"` injects only a number (no string-injection risk). `tool`/`args` are escaped for `<`.

- [ ] **Step 3: Pass the chosen mode on background launch**

In the existing `agLaunch.onclick` handler (the "run in background" button), include the selected mode in the POST body. Change the body it sends to `/run` so it includes `mode: document.getElementById("agMode").value`. For example if it currently sends `{name,model,prompt}`, make it `{name,model,prompt,mode:sessEl?sessEl("agMode").value:document.getElementById("agMode").value}` - simplest is `mode: document.getElementById("agMode").value`.

- [ ] **Step 4: Validate**

Run:
```bash
python -c "h=open('webui/static/command.html',encoding='utf-8').read(); assert 'refreshPending' in h and 'decideAction' in h and 'id=\"pending\"' in h and '/decide' in h; assert h.count('<script')==h.count('</script>'); print('approvals UI OK')"
```
Expected: `approvals UI OK`.

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/static/command.html
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: cockpit approvals queue for background workers + pass launch mode"
```

---

### Task 4: Unsandbox background units + mode passthrough + kill switch

**Files:**
- Modify: `nixos/modules/spawn.nix`
- Modify: `webui/hearth_mapd.py` (the `/run` handler adds mode; `/stop-all` stops background units)

- [ ] **Step 1: mode in the queue request and `/stop-all` extension (`webui/hearth_mapd.py`)**

In `_handle_run`, read a mode and include it in the queued JSON. Change the request parsing to add:
```python
        mode = req.get("mode") or "bypass"
        if mode not in ("plan", "auto", "bypass"):
            mode = "bypass"
```
and change the `json.dump(...)` line to include mode:
```python
                json.dump({"name": name, "model": model, "prompt": prompt, "mode": mode}, fh)
```
Extend `_handle_stop_all` so it also stops running background units. Replace its body with:
```python
    def _handle_stop_all(self):
        with SESSIONS_LOCK:
            sessions = list(SESSIONS.values())
        for sess in sessions:
            sess.stop()
        units = 0
        try:
            out = subprocess.run(
                ["systemctl", "list-units", "--plain", "--no-legend", "hearth-agent@*.service"],
                capture_output=True, text=True, timeout=5).stdout
            names = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
            for name in names:
                subprocess.run(["sudo", "-n", "systemctl", "stop", name], timeout=10)
                units += 1
        except (OSError, subprocess.SubprocessError):
            pass
        return self._send(200, json.dumps({"stopped_sessions": len(sessions),
                                            "stopped_units": units}), "application/json")
```
(mapd runs as operator with passwordless sudo, from Plan 2, so `sudo -n systemctl stop` works.)

- [ ] **Step 2: Unsandbox the `hearth-agent@` template and pass mode + db transport (`nixos/modules/spawn.nix`)**

In the `runner` shell text, read the mode and exec the loop in DB mode. Replace the runner `text` body so it reads mode (default bypass) and execs with `--mode` and `--io db` and an explicit `--db`:
```nix
    text = ''
      id="$1"
      req="/var/lib/hearth/queue/$id.json"
      [ -f "$req" ] || exit 0
      model="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('model','qwen2.5-coder'))" "$req")"
      name="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name','agent'))" "$req")"
      mode="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('mode','bypass'))" "$req")"
      prompt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('prompt') or chr(0)*0)" "$req")"
      rm -f "$req"
      ws="/var/lib/hearth/agents/$id"
      mkdir -p "$ws"
      exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$id" --model "$model" --mode "$mode" --io db --workspace "$ws" --db /var/lib/hearth/runs/audit.db "$prompt"
    '';
```
(Note: pass `--agent-name "$id"` not `"$name"` so each background worker is a distinct agent on the map and in the transcript/pending tables. The friendly name is the prefix of the id.)

Replace the `serviceConfig` of `systemd.services."hearth-agent@"` so the unit runs unsandboxed as operator with full reach (mirroring the mapd decision in Plan 2). Replace:
```nix
    serviceConfig = config.hearth.sandbox.profile // {
      Type = "oneshot";
      ReadWritePaths = config.hearth.sandbox.profile.ReadWritePaths ++ [ "/var/lib/hearth/queue" ];
      LoadCredential = [ "creds:/var/lib/hearth/secrets/agent-credentials" ];
      ExecStart = "${runner}/bin/hearth-run-from-queue %i";
    };
```
with:
```nix
    serviceConfig = {
      Type = "oneshot";
      # Background workers are meant to act on the real machine (the user chose
      # full-machine reach). They run unsandboxed as operator with sudo available,
      # like the mapd-hosted interactive sessions. Containment is the audit log
      # plus the approvals queue (auto mode) and the kill switch.
      User = "operator";
      Group = "users";
      NoNewPrivileges = false;
      # Stored API credentials are still delivered through systemd's credential
      # channel (readable at $CREDENTIALS_DIRECTORY/creds), not world-readable.
      LoadCredential = [ "creds:/var/lib/hearth/secrets/agent-credentials" ];
      ExecStart = "${runner}/bin/hearth-run-from-queue %i";
    };
```
Leave the `systemd.tmpfiles.rules`, `systemd.paths.hearth-spawn`, and `systemd.services.hearth-spawn` blocks unchanged.

- [ ] **Step 3: Verify the Python self-tests still pass**

Run: `python webui/hearth_mapd.py --self-test` and `python agent/hearth_loop.py --self-test`. Both must print their OK line. (The nix change is evaluated on the blade in Task 5.)

- [ ] **Step 4: Sanity-check the nix references**

Confirm `config.hearth.agents.loopPackage` is still referenced and that `config.hearth.sandbox.profile` is no longer referenced in `spawn.nix` (we removed the only use; that is fine, the option still exists for other modules). Re-read `spawn.nix` for balanced braces.

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add nixos/modules/spawn.nix webui/hearth_mapd.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: unsandbox background workers (operator, full reach), mode passthrough, kill switch"
```

---

### Task 5: Deploy to the blade, verify, push (deferred until the blade is reachable)

**Files:** none. DEFERRED: the blade is currently offline. Run this task only once `ssh operator@192.168.1.64 echo up` succeeds.

- [ ] **Step 1: Local self-tests gate**
```bash
python agent/permissions.py
python agent/hearth_state.py --self-test
python agent/hearth_loop.py --self-test
python webui/hearth_mapd.py --self-test
```
All four must print their OK line.

- [ ] **Step 2: Deploy**
```bash
cd C:/Users/ericc/hearth-wt
git archive -o C:/Users/ericc/AppData/Local/Temp/wt.tar HEAD
for i in 1 2 3; do scp -o ConnectTimeout=25 C:/Users/ericc/AppData/Local/Temp/wt.tar operator@192.168.1.64:~/wt.tar && break || sleep 8; done
ssh -o ConnectTimeout=30 operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -3'
```
Expected: a new closure path, no eval error. On error, capture and report BLOCKED.

- [ ] **Step 3: Verify a background worker in bypass runs and is transcribed**
```bash
ssh operator@192.168.1.64 'set +e
rm -f /tmp/hearth_bg_proof.txt
curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"bg\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"prompt\":\"Use run_command to run exactly: echo bg-ran > /tmp/hearth_bg_proof.txt\"}"
echo; sleep 45
echo "=== proof ==="; cat /tmp/hearth_bg_proof.txt 2>&1
echo "=== transcript rows ==="; python3 -c "import sqlite3;c=sqlite3.connect(\"/var/lib/hearth/runs/audit.db\");[print(r[0][:80]) for r in c.execute(\"select event from agent_transcript order by id desc limit 6\")]"'
```
Expected: `/tmp/hearth_bg_proof.txt` contains `bg-ran`, and transcript rows exist for the worker.

- [ ] **Step 4: Verify an auto worker parks a pending approval, then approve it**
```bash
ssh operator@192.168.1.64 'set +e
curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"bgauto\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"auto\",\"prompt\":\"Use run_command to run exactly: whoami\"}"; echo
sleep 40
echo "=== pending ==="; curl -s localhost:8770/pending
PID=$(curl -s localhost:8770/pending | python3 -c "import sys,json;p=json.load(sys.stdin)[\"pending\"];print(p[0][\"id\"] if p else \"\")")
echo "pending id: $PID"
[ -n "$PID" ] && curl -s -X POST localhost:8770/decide -H "Content-Type: application/json" -d "{\"id\":$PID,\"allow\":true}"; echo
sleep 8
echo "=== pending after decide (should be empty) ==="; curl -s localhost:8770/pending'
```
Expected: a pending action appears with tool `run_command`; after `/decide`, the worker proceeds and `/pending` is empty.

- [ ] **Step 5: Verify the kill switch**
```bash
ssh operator@192.168.1.64 'curl -s -X POST localhost:8770/stop-all; echo; systemctl list-units "hearth-agent@*.service" --no-legend --plain | cat'
```
Expected: `/stop-all` returns counts; no background units remain running.

- [ ] **Step 6: Push to main**
```bash
cd C:/Users/ericc/hearth-wt
git fetch origin
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before background workers"
git push origin worktree-desktop:main
```

---

## Self-Review

**Spec coverage (this plan's slice):**
- Background workers stream their transcript to the DB (`agent_transcript`) and gate via `pending_actions` - Task 1.
- mapd `/pending`, `/decide`, `/transcript` endpoints - Task 2.
- Cockpit approvals queue + launch mode passthrough - Task 3.
- Unsandboxed background units (operator, full reach), mode in the queue request, and the kill switch (`/stop-all` stops sessions and background units) - Task 4.
- Deploy + verification of bypass run, auto pending-then-approve, and the kill switch - Task 5 (deferred until the blade is reachable).

**Decisions honored:** background defaults to bypass and is configurable to auto (the "Both / configurable" choice); full-machine reach via operator units; interactive approvals (Plan 2) untouched.

**Placeholder scan:** no TBD/TODO; every code step has complete code; verification steps have exact commands and expected output. The only deferral (Task 5) is explicit and gated on connectivity, not a placeholder.

**Type/name consistency:** `make_db_transport(db, agent_id, poll_interval)` returns `(emit, control)`; tables `agent_transcript(agent_id, ts, event)` and `pending_actions(agent_id, req_id, tool, args, risk, created_at, decision)` are defined identically in `hearth_loop.TRANSCRIPT_SCHEMA` and `hearth_mapd.PENDING_SCHEMA`; mapd helpers `read_pending`/`decide_action`/`read_transcript`; routes `/pending`, `/decide`, `/transcript`; the loop `--io db` path and the spawn runner both use `--agent-name "$id"` so transcript/pending `agent_id` matches what the UI shows. The `/run` queue request gains `mode` (default bypass), consumed by the spawn runner's `--mode`.
