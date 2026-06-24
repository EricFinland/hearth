# hearth Agent Control: Interactive Sessions + Cockpit Console (Plan 2 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the cockpit drive a live agent: open an interactive session from the web UI, watch the agent's work stream in, approve or deny risky steps inline, switch permission modes, and stop it. This is what makes "launch" feel alive instead of dead.

**Architecture:** `hearth-mapd` (the stdlib web server) spawns `hearth-loop --session` (built in Plan 1) as a managed child subprocess, one per session. A reader thread pumps the child's stdout JSON events into an in-memory buffer; an SSE endpoint relays them to the browser; a send endpoint writes JSON control commands to the child's stdin. The `/command` page gains a session console. To give interactive agents full-machine reach, the mapd systemd unit runs as the `operator` user (with sudo available) and drops the strict sandbox, staying localhost + bearer-token gated.

**Tech Stack:** Python 3 standard library only (http.server, subprocess, threading, json, urllib). Frontend is the existing vanilla-JS `command.html`. Tests use the in-module `_self_test()` convention run via `python <module> --self-test` (no pytest). NixOS module edit for the unit. On the dev machine (Windows) run Python as `python`.

**Scope note:** Plan 2 of 3 (spec: `docs/superpowers/specs/2026-06-22-hearth-agent-control.md`; Plan 1 delivered the permission engine + `hearth-loop` control protocol and is merged). Plan 3 will upgrade background workers to stream over the DB (`pending_actions`), unsandbox the `hearth-agent@` units in `nixos/modules/spawn.nix`, and add the global kill switch at the systemd level. This plan delivers working, demoable software: a fully interactive agent session in the browser on the blade.

**Decision (locked with the user):** the cockpit server runs as `operator`; child agents inherit operator + passwordless sudo = full-machine reach. The server stays localhost + bearer-token gated (`request_allowed`, unchanged from Plan 1).

**Commit identity (required):** every commit MUST be authored as Eric and contain no AI attribution. Use exactly:
`git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<message>"`
No em-dashes in any committed file or message.

**Working directory:** the worktree `C:/Users/ericc/hearth-wt` (branch `worktree-desktop`). Paths below are relative to it. Deploy/verify happens on the blade: `ssh operator@192.168.1.64` (key auth; passwordless sudo). Deploy via `git archive -o file.tar HEAD` + `scp` + `sudo nixos-rebuild switch --flake ~/hearth-desktop#blade` (NOT a `git archive | ssh tar` pipe, which corrupts on the flaky WiFi). Clear a stuck rebuild unit first with `sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service`.

---

### Task 1: Session machinery in hearth-mapd (subprocess + reader thread)

Add a `Session` class that owns one `hearth-loop --session` child, a thread that pumps its stdout events into a buffer, and a `send`/`snapshot`/`stop` interface. Plus a process-wide session registry. Pure machinery, tested against a stub child (no Ollama, no hearth-loop needed).

**Files:**
- Modify: `webui/hearth_mapd.py`

- [ ] **Step 1: Write the failing self-test**

In `webui/hearth_mapd.py`, the existing `_self_test()` ends with `print("hearth-mapd self-test OK")` then `return 0`. Insert the following block immediately BEFORE that `print`:

```python
    # --- Session machinery: spawn a stub child that emits a JSON event and echoes
    # one line of input. Proves the reader thread buffers events and send() writes
    # to the child's stdin. No Ollama or hearth-loop needed.
    import sys as _sys
    import time as _time
    child = [_sys.executable, "-c",
             "import sys,json;"
             "print(json.dumps({'type':'state','state':'IDLE'}),flush=True);"
             "line=sys.stdin.readline();"
             "print(json.dumps({'type':'echo','got':line.strip()}),flush=True)"]
    proc = subprocess.Popen(child, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    sess = Session("t-1", proc)
    got_state = False
    for _ in range(100):
        evs, _closed = sess.snapshot(0)
        if any(e.get("type") == "state" for e in evs):
            got_state = True
            break
        _time.sleep(0.05)
    assert got_state, ("expected a state event from the child", sess.snapshot(0))
    assert sess.send({"type": "user_message", "text": "ping"}) is True
    got_echo = None
    for _ in range(100):
        evs, _closed = sess.snapshot(0)
        echo = [e for e in evs if e.get("type") == "echo"]
        if echo:
            got_echo = echo[0]
            break
        _time.sleep(0.05)
    assert got_echo and "ping" in got_echo.get("got", ""), ("expected echo of input", sess.snapshot(0))
    sess.stop()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python webui/hearth_mapd.py --self-test`
Expected: `NameError: name 'Session' is not defined`.

- [ ] **Step 3: Add the imports**

Near the top of `webui/hearth_mapd.py`, with the other imports, add:
```python
import threading
```
(`subprocess`, `json`, `time`, `uuid`, `os` are already imported.)

- [ ] **Step 4: Add the `Session` class and registry**

Add this above the `class Handler(` definition:

```python
class Session:
    """One interactive agent run: a `hearth-loop --session` child process whose
    stdout JSON events are pumped into an in-memory buffer by a reader thread, and
    whose stdin receives JSON control commands. Thread-safe."""

    def __init__(self, sid, proc):
        self.sid = sid
        self.proc = proc
        self.events = []
        self.lock = threading.Lock()
        self.closed = False
        self._reader = threading.Thread(target=self._pump, daemon=True)
        self._reader.start()

    def _pump(self):
        try:
            for line in self.proc.stdout:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except ValueError:
                    ev = {"type": "log", "line": line}
                with self.lock:
                    self.events.append(ev)
        finally:
            with self.lock:
                self.closed = True
                self.events.append({"type": "closed"})

    def send(self, cmd):
        """Write one control command to the child's stdin. Returns False if the
        child's stdin is already gone."""
        try:
            self.proc.stdin.write(json.dumps(cmd) + "\n")
            self.proc.stdin.flush()
            return True
        except (BrokenPipeError, ValueError, OSError):
            return False

    def snapshot(self, start):
        """Return (events_from_index_start, closed_flag)."""
        with self.lock:
            return list(self.events[start:]), self.closed

    def stop(self):
        self.send({"type": "stop"})
        try:
            self.proc.stdin.close()
        except OSError:
            pass


# Process-wide registry of live sessions, keyed by session id.
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def spawn_session(loop_cmd, sid, model, mode, workspace, db, ollama_url):
    """Start a hearth-loop --session child and wrap it in a Session."""
    os.makedirs(workspace, exist_ok=True)
    args = [loop_cmd, "--session", "--model", model, "--mode", mode,
            "--agent-name", sid, "--workspace", workspace, "--db", db,
            "--ollama-url", ollama_url]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, bufsize=1)
    return Session(sid, proc)
```

- [ ] **Step 5: Run to verify the self-test passes**

Run: `python webui/hearth_mapd.py --self-test`
Expected: `hearth-mapd self-test OK` (all prior asserts plus the new Session block), exit 0.

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/hearth_mapd.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: session subprocess machinery in hearth-mapd (reader thread + registry)"
```

---

### Task 2: Session HTTP endpoints

Wire the session machinery into the HTTP handler: create a session, stream its events over SSE, send it commands, and a stop-all. Add a `--loop-cmd` argument so the unit can point at the packaged `hearth-loop` binary.

**Files:**
- Modify: `webui/hearth_mapd.py`

- [ ] **Step 1: Add the `--loop-cmd` argument and Handler attribute**

In `main()`, add an argument near the others:
```python
    parser.add_argument("--loop-cmd", default=os.environ.get("HEARTH_LOOP_CMD", "hearth-loop"),
                        help="command used to spawn an interactive agent loop")
```
Pass it into the server factory. Change `make_server` to accept and set it:
```python
def make_server(host, port, db, static_dir, loop_cmd="hearth-loop"):
    Handler.db = db
    Handler.static_dir = static_dir
    Handler.loop_cmd = loop_cmd
    return ThreadingHTTPServer((host, port), Handler)
```
Update the `main()` call site:
```python
    server = make_server(args.host, args.port, args.db, args.static_dir, args.loop_cmd)
```
And add the class attribute on `Handler` next to the existing `db`/`static_dir`:
```python
    loop_cmd = "hearth-loop"
    ollama_url = OLLAMA_URL
```
(`OLLAMA_URL` is already a module global.)

- [ ] **Step 2: Add the POST routes**

In `do_POST`, after the existing `if path == "/run": return self._handle_run()` line, add:
```python
        if path == "/session":
            return self._handle_new_session()
        if path == "/stop-all":
            return self._handle_stop_all()
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "send":
            return self._handle_session_send(parts[1])
```

- [ ] **Step 3: Add the GET route for session events**

In `do_GET`, just before the final `return self._send(404, "not found")`, add:
```python
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "events":
            return self._serve_session_events(parts[1])
```

- [ ] **Step 4: Implement the handlers**

Add these methods to the `Handler` class (next to `_handle_run`):

```python
    def _handle_new_session(self):
        req = self._read_json_body()
        name = (req.get("name") or "session").replace("/", "_").replace(" ", "_")[:40] or "session"
        model = req.get("model") or "llama3.2:3b"
        mode = req.get("mode") or "auto"
        if mode not in ("plan", "auto", "bypass"):
            mode = "auto"
        task = req.get("task") or ""
        sid = "{}-{}".format(name, uuid.uuid4().hex[:8])
        workspace = "/var/lib/hearth/agents/" + sid
        try:
            sess = spawn_session(self.loop_cmd, sid, model, mode, workspace,
                                 self.db, self.ollama_url)
        except OSError as exc:
            return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        with SESSIONS_LOCK:
            SESSIONS[sid] = sess
        if task:
            sess.send({"type": "user_message", "text": task})
        return self._send(200, json.dumps({"id": sid, "mode": mode, "model": model}),
                          "application/json")

    def _handle_session_send(self, sid):
        req = self._read_json_body()
        with SESSIONS_LOCK:
            sess = SESSIONS.get(sid)
        if sess is None:
            return self._send(404, json.dumps({"error": "no such session"}), "application/json")
        ok = sess.send(req)
        return self._send(200, json.dumps({"sent": ok}), "application/json")

    def _handle_stop_all(self):
        with SESSIONS_LOCK:
            sessions = list(SESSIONS.values())
        for sess in sessions:
            sess.stop()
        return self._send(200, json.dumps({"stopped": len(sessions)}), "application/json")

    def _serve_session_events(self, sid):
        with SESSIONS_LOCK:
            sess = SESSIONS.get(sid)
        if sess is None:
            return self._send(404, json.dumps({"error": "no such session"}), "application/json")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        idx = 0
        try:
            while True:
                evs, closed = sess.snapshot(idx)
                for ev in evs:
                    idx += 1
                    self.wfile.write(("data: " + json.dumps(ev) + "\n\n").encode())
                self.wfile.flush()
                if closed and idx >= len(sess.events):
                    # drop the finished session from the registry once drained
                    with SESSIONS_LOCK:
                        SESSIONS.pop(sid, None)
                    return
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return
```

- [ ] **Step 5: Verify the self-test still passes and do a manual HTTP smoke test**

Run: `python webui/hearth_mapd.py --self-test` -> expect `hearth-mapd self-test OK`, exit 0.

Manual HTTP test using a stub loop command (no Ollama). In one terminal start the server pointed at a stub loop that behaves like `hearth-loop --session` (emits an IDLE state then echoes), using a writable temp dir for the db:

```bash
mkdir -p /tmp/hearth-mapd-test
cat > /tmp/hearth-mapd-test/stubloop.py <<'PY'
import sys, json
print(json.dumps({"type": "state", "state": "IDLE", "detail": "ready"}), flush=True)
for line in sys.stdin:
    cmd = json.loads(line)
    if cmd.get("type") == "stop":
        break
    if cmd.get("type") == "user_message":
        print(json.dumps({"type": "message", "role": "assistant",
                          "content": "you said: " + cmd.get("text", "")}), flush=True)
print(json.dumps({"type": "done"}), flush=True)
PY
python webui/hearth_mapd.py --port 8771 --db /tmp/hearth-mapd-test/d.db \
  --loop-cmd "python /tmp/hearth-mapd-test/stubloop.py" &
sleep 1
```
Note: `--loop-cmd` must be a single executable path in the real unit; for THIS stub test the two-word command will not work via `subprocess.Popen(list)` because `spawn_session` builds an arg list with `loop_cmd` as a single element. So instead make the stub directly executable and pass its path. Replace the launch with:
```bash
printf '#!/usr/bin/env python3\n' > /tmp/hearth-mapd-test/stubloop
cat /tmp/hearth-mapd-test/stubloop.py >> /tmp/hearth-mapd-test/stubloop
chmod +x /tmp/hearth-mapd-test/stubloop
python webui/hearth_mapd.py --port 8771 --db /tmp/hearth-mapd-test/d.db \
  --loop-cmd /tmp/hearth-mapd-test/stubloop &
sleep 1
```
Then drive it:
```bash
SID=$(curl -s -X POST localhost:8771/session -H 'Content-Type: application/json' \
  -d '{"name":"t","model":"mock","mode":"auto","task":"hello"}' | python -c "import sys,json;print(json.load(sys.stdin)['id'])")
echo "session: $SID"
curl -s -X POST localhost:8771/session/$SID/send -H 'Content-Type: application/json' -d '{"type":"user_message","text":"again"}'
# read a couple seconds of the event stream:
curl -s --max-time 2 localhost:8771/session/$SID/events || true
curl -s -X POST localhost:8771/session/$SID/send -H 'Content-Type: application/json' -d '{"type":"stop"}'
kill %1 2>/dev/null || true
```
Expected: `/session` returns an `id`; the `/events` stream prints `data: {"type": "state", "state": "IDLE", ...}` and `data: {"type": "message", ... "you said: hello"}` and the echo of "again". This proves create + stream + send work end to end. (On Windows the `&` backgrounding and shebang execution work under the Bash tool; if shebang exec fails on this platform, note it and rely on the blade verification in Task 5 instead.)

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/hearth_mapd.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: session endpoints in hearth-mapd (create, SSE events, send, stop-all)"
```

---

### Task 3: Cockpit session console (command.html)

Add the mode selector and an "open session" action to the launch panel, and a session console that streams the transcript, renders approve/deny cards for gated tools, sends follow-up messages, switches mode live, and stops.

**Files:**
- Modify: `webui/static/command.html`

- [ ] **Step 1: Add mode select + open-session button to the launch panel**

In the `#launch` card (currently has `agName`, `agModel`, `agTask`, the `agLaunch` button, and `agMsg`), change the markup so it has a mode selector and two buttons. Replace the launch card's inner markup (from the `agModel` select through the `agMsg` div) with:

```html
    <select id="agModel" style="width:100%;margin-bottom:4px;"></select>
    <select id="agMode" style="width:100%;margin-bottom:4px;">
      <option value="plan">plan (look only)</option>
      <option value="auto" selected>auto (ask on risky)</option>
      <option value="bypass">bypass (no prompts)</option>
    </select>
    <textarea id="agTask" placeholder="task for the agent..." style="width:100%;height:60px;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;box-sizing:border-box;"></textarea>
    <div style="display:flex;gap:6px;margin-top:4px;">
      <button id="agSession" style="flex:1;background:#1c6;color:#04150c;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">open session</button>
      <button id="agLaunch" style="flex:1;background:#16324f;color:#cfe6ff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">run in background</button>
    </div>
    <div id="agMsg" style="font-size:12px;margin-top:6px;"></div>
```

- [ ] **Step 2: Add the session console card markup**

Add a new console card. Inside the `#grid` div, after the `#map` card, add:

```html
  <div class="card" id="session" style="display:none;grid-column:2/3;grid-row:1/4;flex-direction:column;">
    <div class="title" style="display:flex;justify-content:space-between;align-items:center;">
      <span>session <span id="sessId" style="color:#8a93a0;"></span></span>
      <span>
        <select id="sessMode" style="background:#0e2236;border:1px solid #16324f;color:#cfe6ff;border-radius:6px;padding:2px;">
          <option value="plan">plan</option><option value="auto">auto</option><option value="bypass">bypass</option>
        </select>
        <button id="sessStop" style="background:#a33;color:#fff;border:0;border-radius:6px;padding:4px 10px;cursor:pointer;">stop</button>
        <button id="sessClose" style="background:#16324f;color:#cfe6ff;border:0;border-radius:6px;padding:4px 10px;cursor:pointer;">close</button>
      </span>
    </div>
    <div id="sessBanner" style="display:none;background:#5a1414;color:#ffd7d7;padding:4px 8px;border-radius:6px;margin-bottom:6px;font-size:12px;">BYPASS MODE: this agent runs every action without asking.</div>
    <div id="sessLog" style="flex:1;overflow:auto;font-size:12px;line-height:1.5;"></div>
    <div style="display:flex;gap:6px;margin-top:6px;">
      <input id="sessInput" placeholder="send a follow-up to the agent..." style="flex:1;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;" />
      <button id="sessSend" style="background:#16324f;color:#cfe6ff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">send</button>
    </div>
  </div>
```

Note: the `#session` card sits in the same grid cell as `#map` (`grid-column:2/3;grid-row:1/4`) and is shown (display:flex) only while a session is open, hiding the map; closing it hides the console and shows the map again.

- [ ] **Step 3: Add the session console JavaScript**

At the end of the existing `<script>` block (after the `agLaunch.onclick` handler), add:

```javascript
// ---- interactive session console ----
let sessionES=null, sessionId=null;
const sessEl=id=>document.getElementById(id);
function sessAppend(html){const l=sessEl("sessLog");l.innerHTML+=html;l.scrollTop=l.scrollHeight;}
function esc(s){return (s||"").replace(/&/g,"&amp;").replace(/</g,"&lt;");}
function showSession(on){
  sessEl("session").style.display=on?"flex":"none";
  document.getElementById("map").style.display=on?"none":"";
}
function decide(id,allow){
  fetch(`/session/${sessionId}/send`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({type:"decision",id,allow})});
}
function renderEvent(d){
  if(d.type==="message"){sessAppend(`<div><b style="color:#6fcf7f">agent:</b> ${esc(d.content)}</div>`);}
  else if(d.type==="tool_result"){
    const tag=d.denied?'<span style="color:#e06a6a">denied</span>':'<span style="color:#5fd0ff">ran</span>';
    sessAppend(`<div style="opacity:.85">${tag} <b>${esc(d.tool)}</b><pre style="white-space:pre-wrap;margin:2px 0;color:#9fb6cc">${esc(d.output||"")}</pre></div>`);}
  else if(d.type==="tool_request"){
    sessAppend(`<div id="req_${d.id}" style="border:1px solid #6a5a1a;background:#241f0e;border-radius:6px;padding:6px;margin:4px 0;">
      <div><b style="color:#f0a030">approve?</b> <b>${esc(d.tool)}</b> <small style="color:#8a93a0">(${esc(d.risk)})</small></div>
      <pre style="white-space:pre-wrap;margin:4px 0;color:#cfe6ff">${esc(JSON.stringify(d.args))}</pre>
      <button onclick="decide('${d.id}',true);this.parentNode.remove();" style="background:#1c6;color:#04150c;border:0;border-radius:6px;padding:3px 10px;cursor:pointer;">approve</button>
      <button onclick="decide('${d.id}',false);this.parentNode.remove();" style="background:#a33;color:#fff;border:0;border-radius:6px;padding:3px 10px;cursor:pointer;margin-left:6px;">deny</button>
    </div>`);}
  else if(d.type==="plan"){sessAppend(`<div style="border-left:3px solid #5fd0ff;padding-left:8px;margin:4px 0;"><b style="color:#5fd0ff">plan</b><pre style="white-space:pre-wrap;margin:2px 0">${esc(d.content)}</pre></div>`);}
  else if(d.type==="state"){if(d.state==="WAITING_APPROVAL")sessAppend(`<div style="color:#f0a030;font-size:11px">awaiting approval...</div>`);}
  else if(d.type==="notice"){sessAppend(`<div style="color:#f0a030;font-size:11px">${esc(d.detail)}</div>`);}
  else if(d.type==="turn_done"){if(d.error)sessAppend(`<div style="color:#e06a6a;font-size:11px">turn ended: ${esc(d.error)}</div>`);}
  else if(d.type==="closed"||d.type==="done"){sessAppend(`<div style="color:#8a93a0;font-size:11px">[session ended]</div>`);}
}
function openSession(id,mode){
  sessionId=id; showSession(true);
  sessEl("sessLog").innerHTML=""; sessEl("sessId").textContent=id;
  sessEl("sessMode").value=mode; sessEl("sessBanner").style.display=mode==="bypass"?"block":"none";
  if(sessionES)sessionES.close();
  sessionES=new EventSource(`/session/${id}/events`);
  sessionES.onmessage=m=>{try{renderEvent(JSON.parse(m.data));}catch(e){}};
}
sessEl("agSession").onclick=async()=>{
  const name=sessEl("agName").value.trim()||"session";
  const model=sessEl("agModel").value, mode=sessEl("agMode").value;
  const task=sessEl("agTask").value.trim();
  sessEl("agMsg").textContent="opening session...";
  try{const r=await fetch("/session",{method:"POST",headers:{"Content-Type":"application/json"},
      body:JSON.stringify({name,model,mode,task})});
    const j=await r.json();
    if(j.id){sessEl("agMsg").textContent="session open"; sessEl("agTask").value=""; openSession(j.id,j.mode);}
    else sessEl("agMsg").textContent="error: "+(j.error||"unknown");
  }catch(e){sessEl("agMsg").textContent="error: "+e;}
};
sessEl("sessSend").onclick=()=>{
  const t=sessEl("sessInput").value.trim(); if(!t||!sessionId)return;
  sessEl("sessInput").value=""; sessAppend(`<div><b style="color:#5fd0ff">you:</b> ${esc(t)}</div>`);
  fetch(`/session/${sessionId}/send`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({type:"user_message",text:t})});
};
sessEl("sessInput").addEventListener("keydown",e=>{if(e.key==="Enter")sessEl("sessSend").onclick();});
sessEl("sessMode").onchange=()=>{
  if(!sessionId)return; const m=sessEl("sessMode").value;
  sessEl("sessBanner").style.display=m==="bypass"?"block":"none";
  fetch(`/session/${sessionId}/send`,{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({type:"set_mode",mode:m})});
};
sessEl("sessStop").onclick=()=>{if(sessionId)fetch(`/session/${sessionId}/send`,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({type:"stop"})});};
sessEl("sessClose").onclick=()=>{if(sessionES)sessionES.close(); showSession(false);};
```

Also fix the dead-feeling background-launch: in the existing `agLaunch.onclick` handler, after a successful queue, clear the task field. Find the line that sets `msg.textContent=j.queued?...` and immediately after the success case set `document.getElementById("agTask").value="";`.

- [ ] **Step 4: Manual UI smoke test (static, no server needed)**

Open `webui/static/command.html` in a browser locally OR just validate the HTML/JS parse by running:
```bash
python -c "import re,sys; html=open('webui/static/command.html',encoding='utf-8').read(); assert 'agSession' in html and 'openSession' in html and 'sessLog' in html and 'decide(' in html; print('command.html markup+JS present OK')"
```
Expected: `command.html markup+JS present OK`. (Full interactive behavior is verified on the blade in Task 5; this step just guards against a missing element id or obvious truncation.)

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add webui/static/command.html
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: cockpit session console (live transcript, approve/deny, mode switch, stop)"
```

---

### Task 4: Run mapd as operator with full reach

Change the `hearth-mapd` systemd unit to run as `operator`, drop the strict sandbox (so child agents reach the real machine and can use sudo), and put the packaged `hearth-loop` on its PATH so sessions can spawn it.

**Files:**
- Modify: `nixos/modules/mapui.nix`

- [ ] **Step 1: Update the unit**

In `nixos/modules/mapui.nix`, replace the `serviceConfig` block (currently `User = "hearth"` with `NoNewPrivileges`, `ProtectHome`, `ProtectSystem = "strict"`, `ReadWritePaths`, `PrivateTmp`) with a configuration that runs as operator with full reach. Replace the whole `systemd.services.hearth-mapd` definition with:

```nix
    systemd.services.hearth-mapd = {
      description = "hearth tycoon map backend + agent session host";
      after = [ "network.target" "hearth-audit-init.service" ];
      wantedBy = [ "multi-user.target" ];
      # The packaged agent loop must be on PATH so the server can spawn
      # `hearth-loop --session` children for interactive sessions.
      path = [ config.hearth.agents.loopPackage ];
      serviceConfig = {
        ExecStart = "${hearthMapd}/bin/hearth-mapd --host 0.0.0.0 --port ${toString cfg.port} --db /var/lib/hearth/runs/audit.db --loop-cmd ${config.hearth.agents.loopPackage}/bin/hearth-loop";
        # Interactive agents are meant to act on the real machine (the user chose
        # full-machine reach). The server therefore runs as the operator user with
        # sudo available, and the strict sandbox is intentionally NOT applied here.
        # Containment is by network instead: the server is localhost + bearer-token
        # gated (see request_allowed), and every action is written to the audit DB.
        User = "operator";
        Group = "users";
        Restart = "on-failure";
        EnvironmentFile = [ "-/var/lib/hearth/secrets/mapd.env" ];
        # Children invoke sudo; do not block privilege escalation.
        NoNewPrivileges = false;
      };
    };
```

Note: do not keep `ProtectSystem`/`ProtectHome`/`ReadWritePaths`/`PrivateTmp` here. They would defeat full-machine reach. The agent workspace `/var/lib/hearth/agents/<id>` is group-writable by `hearth` (mode 2770) and `operator` is in the `hearth` group, so session workspaces and the audit DB remain writable.

- [ ] **Step 2: Sanity-check the reference is valid**

Confirm `config.hearth.agents.loopPackage` is the option used elsewhere for the loop binary by checking it is referenced in `nixos/modules/spawn.nix` (it is: `${config.hearth.agents.loopPackage}/bin/hearth-loop`). No code to run on Windows here; the real evaluation happens on the blade in Task 5 (`nixos-rebuild`). If `config.hearth.agents.loopPackage` does not exist, STOP and report BLOCKED (do not invent an option name); the correct package option must be found in `nixos/modules/agents.nix` first.

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add nixos/modules/mapui.nix
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: run hearth-mapd as operator with full reach to host interactive sessions"
```

---

### Task 5: Deploy to the blade and verify end to end, then push

**Files:** none (deploy + verification + git). This task runs commands against the live blade over SSH.

- [ ] **Step 1: Run every Python self-test locally first**

```bash
python agent/permissions.py
python agent/hearth_state.py --self-test
python agent/hearth_loop.py --self-test
python webui/hearth_mapd.py --self-test
```
Expected: each prints its `... self-test OK` line. If any fails, STOP (report BLOCKED).

- [ ] **Step 2: Deploy to the blade**

```bash
cd C:/Users/ericc/hearth-wt
git archive -o C:/Users/ericc/AppData/Local/Temp/wt.tar HEAD
for i in 1 2 3; do scp -o ConnectTimeout=25 C:/Users/ericc/AppData/Local/Temp/wt.tar operator@192.168.1.64:~/wt.tar && break || sleep 8; done
ssh -o ConnectTimeout=30 operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -3'
```
Expected: the rebuild ends with a new system closure path and no evaluation error. If nixos-rebuild reports an error, capture it and report BLOCKED with the error.

- [ ] **Step 3: Confirm mapd is running as operator**

```bash
ssh operator@192.168.1.64 'systemctl show hearth-mapd -p User,SubState,ActiveState | cat; systemctl is-active hearth-mapd'
```
Expected: `User=operator`, `ActiveState=active`, `SubState=running`.

- [ ] **Step 4: End-to-end session test against real Ollama**

Drive a session via the local API on the blade (localhost is allowed). Use bypass mode so it runs a command without needing an interactive approval click, and confirm the agent actually touched the real machine:

```bash
ssh operator@192.168.1.64 'set +e
SID=$(curl -s -X POST localhost:8770/session -H "Content-Type: application/json" -d "{\"name\":\"verify\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"task\":\"run the shell command: echo hearth-was-here > /tmp/hearth_session_proof.txt\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)[\"id\"])")
echo "session=$SID"
# stream up to ~40s of events (the model needs time)
curl -s --max-time 40 localhost:8770/session/$SID/events | head -40
echo "=== proof file ==="; cat /tmp/hearth_session_proof.txt 2>&1
curl -s -X POST localhost:8770/session/$SID/send -H "Content-Type: application/json" -d "{\"type\":\"stop\"}" >/dev/null'
```
Expected: the event stream shows `state`/`message`/`tool_result` events, and `/tmp/hearth_session_proof.txt` contains `hearth-was-here` (proving an interactive session ran a real command on the actual machine with full reach). If the model is slow, the proof file may appear after the 40s window; re-check the file with a follow-up `ssh ... 'cat /tmp/hearth_session_proof.txt'`.

- [ ] **Step 5: Confirm the gating path (auto mode raises an approval)**

```bash
ssh operator@192.168.1.64 'set +e
SID=$(curl -s -X POST localhost:8770/session -H "Content-Type: application/json" -d "{\"name\":\"gate\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"auto\",\"task\":\"run the shell command: whoami\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)[\"id\"])")
echo "session=$SID"
curl -s --max-time 30 localhost:8770/session/$SID/events | grep -m1 tool_request && echo "GATING OK: a tool_request was raised"
# approve it by replaying the id
curl -s -X POST localhost:8770/session/$SID/send -H "Content-Type: application/json" -d "{\"type\":\"stop\"}" >/dev/null'
```
Expected: a `tool_request` event appears (the agent paused for approval in auto mode) and `GATING OK` prints.

- [ ] **Step 6: Visual confirmation (manual, by the user)**

Open the cockpit on the blade (the hearth app, or a browser at http://localhost:8770/command), type a task, pick a mode, click "open session", and watch the transcript stream with approve/deny cards. This is a human check; note in the report that it is pending user confirmation.

- [ ] **Step 7: Push to main**

```bash
cd C:/Users/ericc/hearth-wt
git fetch origin
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before agent sessions cockpit"
git push origin worktree-desktop:main
```
Expected: clean merge (or already up to date) and a successful push. If a merge conflict occurs, STOP and report BLOCKED with the conflicted files.

---

## Self-Review

**Spec coverage (this plan's slice):**
- Interactive session drive path (mapd spawns hearth-loop child, SSE out, POST in) - Tasks 1, 2.
- Session endpoints `POST /session`, `GET /session/<id>/events`, `POST /session/<id>/send`, plus `POST /stop-all` (the kill switch at the server level) - Task 2.
- Cockpit console: streaming transcript, tool-call cards, inline approve/deny, live mode selector, stop, bypass red banner; launch panel mode selector + open-session vs run-in-background; the dead-form-clear fix - Task 3.
- Full-machine reach via running mapd as operator with the sandbox dropped, localhost + token retained, audit retained - Task 4.
- Verification that a session runs a real command on the machine and that auto mode gates - Task 5.

**Deferred to Plan 3 (intentionally):** background-worker transcript/approvals over the DB (`pending_actions`), unsandboxing the `hearth-agent@` template in `spawn.nix`, and per-unit kill-switch wiring. Background workers still run as today (Plan 1 left them on the existing queue path); this plan does not change them.

**Placeholder scan:** no TBD/TODO; each code step has complete code; each verification step has exact commands and expected output. The one platform caveat (shebang exec of the stub loop on Windows in Task 2 Step 5) is called out with a fallback to the blade verification.

**Type/name consistency:** `Session(sid, proc)` with `.send(cmd)`, `.snapshot(start)`, `.stop()`, `.events`, `.closed`; `SESSIONS` / `SESSIONS_LOCK`; `spawn_session(loop_cmd, sid, model, mode, workspace, db, ollama_url)`; Handler attrs `loop_cmd` / `ollama_url`; endpoints `/session`, `/session/<id>/events`, `/session/<id>/send`, `/stop-all`. Event `type` values rendered by the UI (`message`, `tool_result`, `tool_request`, `plan`, `state`, `notice`, `turn_done`, `done`, `closed`) match what `hearth_loop.py` emits (Plan 1) plus the `closed` sentinel the reader thread appends. Control command shapes (`user_message`/`text`, `decision`/`id`/`allow`, `set_mode`/`mode`, `stop`) match `hearth_loop`'s `run_session` and `_await_decision`.
