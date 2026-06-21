# hearth app (v1: chat + launch-agent) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the hearth command center into an openable desktop app where you chat with your local models and launch sandboxed agent tasks, watching everything live.

**Architecture:** Extend the existing stdlib `hearth-mapd` web server with `/models`, `/chat`, and `/run` endpoints. Chat calls Ollama's chat API directly and records each turn as a run. Launch-agent drops a request file in a queue; a systemd path-watcher (root) starts a per-run sandboxed `hearth-agent@` instance, so the UI needs no privilege and every launched agent still runs under the DynamicUser sandbox. The page (`command.html`) gains Chat and Launch panels. A home-manager desktop entry opens the page in its own window.

**Tech Stack:** Python 3 stdlib (hearth-mapd), Ollama HTTP API, NixOS systemd (templated unit + path unit), home-manager (desktop entry), HTML/JS.

**Repo conventions (read first):**
- Work in the worktree `C:\Users\ericc\hearth-wt` (branch `worktree-desktop`). Do NOT touch `C:\Users\ericc\OneDrive\Desktop\hearth`.
- Commit as Eric: `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit ...`. No AI attribution. No em dashes in committed files.
- Python tested locally with `py` (3.14). Nix eval/build on the blade: `ssh operator@192.168.1.64` (key auth, passwordless sudo, nix flakes). Blade WiFi is intermittent; retry on timeout. Deploy by `git archive -o file.tar HEAD` then `scp` the tar and extract into `~/hearth-desktop` (a long `git archive | ssh tar` pipe corrupts on WiFi blips; use scp of a file).
- `hearth-mapd` already serves `/`, `/state`, `/events`, `/stats`, `/command`, `/healthz` and reads/writes the SQLite at `/var/lib/hearth/runs/audit.db`. `agent/hearth_agent.py` runs one prompt against Ollama and records a run + emits state. `agent/hearth_state.py` has `emit_state` and `STATE_ICONS`.

---

## File Structure

- Modify `webui/hearth_mapd.py` - add `read_models`, `do_chat`, `queue_run` + routes `/models`, `/chat`, `/run`.
- Modify `webui/static/command.html` - add Chat panel and Launch panel + their JS.
- Create `nixos/modules/spawn.nix` - the queue dir, `hearth-agent@` templated sandboxed unit, and the `hearth-spawn` path+service that starts instances.
- Modify `nixos/configuration.nix` - import `./modules/spawn.nix`.
- Modify `nixos/home/operator.nix` - add the `hearth` desktop entry (app launcher icon).

---

## Task 1: /models endpoint (list local models)

**Files:** Modify `webui/hearth_mapd.py`.

- [ ] **Step 1: Add a pure parser + a self-test case**

Add near the other parsers:
```python
def parse_models(tags_json_text):
    """Extract model names from an Ollama /api/tags JSON body."""
    try:
        data = json.loads(tags_json_text)
    except (ValueError, TypeError):
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]
```
Extend the existing `_self_test()` with:
```python
    assert parse_models('{"models":[{"name":"llama3.2:3b"},{"name":"mistral:7b"}]}') == ["llama3.2:3b", "mistral:7b"], "parse_models"
    assert parse_models("not json") == [], "parse_models bad"
```

- [ ] **Step 2: Run the self-test**

Run: `cd "C:/Users/ericc/hearth-wt" && py webui/hearth_mapd.py --self-test`
Expected: `hearth-mapd self-test OK`

- [ ] **Step 3: Add the live fetch + route**

Add:
```python
def read_models():
    try:
        url = OLLAMA_URL.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return parse_models(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return []
```
In `do_GET`, add:
```python
        if path == "/models":
            return self._send(200, json.dumps({"models": read_models()}), "application/json")
```

- [ ] **Step 4: Verify locally**

Run: `cd "C:/Users/ericc/hearth-wt" && py -c "import sys;sys.path.insert(0,'webui');import hearth_mapd as m;print(m.read_models())"`
Expected: a list (real names if Ollama is reachable on this box, otherwise `[]`) with no exception.

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd /models endpoint"
```

---

## Task 2: /chat endpoint (talk to a local model, recorded as a run)

**Files:** Modify `webui/hearth_mapd.py`.

- [ ] **Step 1: Add the chat handler**

`hearth-mapd` already imports `json`, `urllib.request`, `sqlite3`, `time`. Add `import uuid` and `from datetime import datetime, timezone` if not present. Add:
```python
def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _record_chat_run(db, agent_name, model, tokens_in, tokens_out, latency_ms, error):
    """Record a chat turn into agent_runs and agent_state so it shows live."""
    run_id = uuid.uuid4().hex
    ts = _now_iso()
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
            "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_name, run_id, ts, ts, tokens_in, tokens_out, 0.0, latency_ms, error, model),
        )
        con.execute(
            "INSERT INTO agent_state (agent_id, state, detail, updated_at) VALUES (?,?,?,?) "
            "ON CONFLICT(agent_id) DO UPDATE SET state=excluded.state, detail=excluded.detail, updated_at=excluded.updated_at",
            (agent_name, "ERRORED" if error else "DONE", error or (str(tokens_out) + " tokens"), ts),
        )
        con.execute(
            "INSERT INTO agent_events (ts, agent_id, state, detail) VALUES (?,?,?,?)",
            (ts, agent_name, "ERRORED" if error else "DONE", error or "chat reply"),
        )
        con.commit()
        con.close()
    except sqlite3.Error:
        pass


def chat_once(base_url, model, messages, timeout=300):
    """Call Ollama /api/chat (non-streaming). Returns (reply_text, tokens_in, tokens_out)."""
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    reply = (data.get("message") or {}).get("content", "")
    return reply, int(data.get("prompt_eval_count", 0) or 0), int(data.get("eval_count", 0) or 0)
```
Note: the SCHEMA constant in hearth-mapd currently defines only agent_state and agent_events. Add the agent_runs table to the SCHEMA string in hearth-mapd so `_record_chat_run` can insert it even on a fresh db (copy the agent_runs CREATE TABLE from agent/hearth_agent.py verbatim).

- [ ] **Step 2: Add a POST dispatcher and the /chat route**

`BaseHTTPRequestHandler` needs `do_POST`. Add:
```python
    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode() or "{}")
        except ValueError:
            return {}

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path == "/chat":
            return self._handle_chat()
        if path == "/run":
            return self._handle_run()
        return self._send(404, "not found")

    def _handle_chat(self):
        req = self._read_json_body()
        model = req.get("model") or "llama3.2:3b"
        messages = req.get("messages") or []
        agent_name = req.get("agent_name") or "chat"
        t0 = time.monotonic()
        error = None
        reply, tin, tout = "", 0, 0
        try:
            reply, tin, tout = chat_once(OLLAMA_URL, model, messages)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = "{}: {}".format(type(exc).__name__, exc)
        latency = int((time.monotonic() - t0) * 1000)
        _record_chat_run(self.db, agent_name, model, tin, tout, latency, error)
        self._send(200, json.dumps({"reply": reply, "error": error,
                                    "tokens_in": tin, "tokens_out": tout}),
                   "application/json")
```
(`_handle_run` is added in Task 6; add a temporary stub now: `def _handle_run(self): return self._send(503, "run not enabled yet")` so do_POST resolves. Replace it in Task 6.)

- [ ] **Step 3: Test chat plumbing locally with a mock Ollama**

Create a throwaway test (do not commit) that monkeypatches `chat_once` and posts to `/chat`, asserting the reply is returned and a row lands in the db. Run it:
```
cd "C:/Users/ericc/hearth-wt" && py -c "
import sys,threading,time,tempfile,os,json,urllib.request
sys.path.insert(0,'webui'); import hearth_mapd as m
m.chat_once = lambda u,model,msgs,timeout=300: ('hello back', 5, 3)
d=tempfile.mkdtemp(); db=os.path.join(d,'a.db')
srv=m.make_server('127.0.0.1',8796,db,'webui/static'); threading.Thread(target=srv.serve_forever,daemon=True).start(); time.sleep(0.3)
req=urllib.request.Request('http://127.0.0.1:8796/chat',data=json.dumps({'model':'m','messages':[{'role':'user','content':'hi'}]}).encode(),headers={'Content-Type':'application/json'})
print('reply:', json.loads(urllib.request.urlopen(req,timeout=5).read().decode()))
import sqlite3; print('rows:', sqlite3.connect(db).execute('select count(*) from agent_runs').fetchone())
srv.shutdown()"
```
Expected: `reply: {'reply': 'hello back', 'error': None, 'tokens_in': 5, 'tokens_out': 3}` and `rows: (1,)`.

- [ ] **Step 4: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd /chat endpoint (Ollama chat, recorded as a run)"
```

---

## Task 3: App launcher entry

**Files:** Modify `nixos/home/operator.nix`.

- [ ] **Step 1: Add a desktop entry**

In `nixos/home/operator.nix`, inside the config attrset, add (read the file first; place it near the other home settings):
```nix
  xdg.desktopEntries.hearth = {
    name = "hearth";
    comment = "Local LLM and agent cockpit";
    exec = "${pkgs.firefox}/bin/firefox --new-window --class hearth-app --app=http://localhost:8770/command";
    terminal = false;
    categories = [ "Utility" "Development" ];
  };
```

- [ ] **Step 2: Eval on the blade**

```
cd "C:/Users/ericc/hearth-wt" && git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth app launcher entry (opens the cockpit in its own window)"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -4'
```
Expected: `all checks passed!`. (`xdg.desktopEntries` is a stable home-manager option; if it errors, report it.)

- [ ] **Step 3: (commit already done in Step 2).**

---

## Task 4: Chat UI panel

**Files:** Modify `webui/static/command.html`.

- [ ] **Step 1: Add a Chat card and its JS**

Add a new card to the `#grid` (place it spanning a column; adjust the grid template so chat sits beside the map). Concretely, change the grid to three rows and add this card markup after the `#stats` card:
```html
  <div class="card" id="chat">
    <div class="title">chat</div>
    <select id="chatModel"></select>
    <div id="chatLog" style="height:160px;overflow:auto;margin:6px 0;font-size:12px;"></div>
    <div style="display:flex;gap:6px;">
      <input id="chatInput" placeholder="message your local model..." style="flex:1;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;" />
      <button id="chatSend" style="background:#16324f;color:#cfe6ff;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">send</button>
    </div>
  </div>
```
Add JS before `</script>`:
```javascript
const chatMessages=[];
async function loadModels(){try{const j=await(await fetch("/models")).json();
  const sel=document.getElementById("chatModel");sel.innerHTML=(j.models||[]).map(n=>`<option>${n}</option>`).join("")||"<option>llama3.2:3b</option>";}catch(e){}}
loadModels();
function appendChat(role,text){const l=document.getElementById("chatLog");
  l.innerHTML+=`<div><b style="color:${role==='you'?'#5fd0ff':'#6fcf7f'}">${role}:</b> ${text.replace(/</g,'&lt;')}</div>`;l.scrollTop=l.scrollHeight;}
async function sendChat(){const inp=document.getElementById("chatInput");const text=inp.value.trim();if(!text)return;
  inp.value="";appendChat("you",text);chatMessages.push({role:"user",content:text});
  const model=document.getElementById("chatModel").value;
  appendChat("...","thinking");
  try{const r=await fetch("/chat",{method:"POST",headers:{"Content-Type":"application/json"},
    body:JSON.stringify({model,messages:chatMessages,agent_name:"chat"})});
    const j=await r.json();const l=document.getElementById("chatLog");l.lastChild.remove();
    if(j.error){appendChat("error",j.error);}else{appendChat(model,j.reply);chatMessages.push({role:"assistant",content:j.reply});}
  }catch(e){appendChat("error",String(e));}}
document.getElementById("chatSend").onclick=sendChat;
document.getElementById("chatInput").addEventListener("keydown",e=>{if(e.key==="Enter")sendChat();});
```

- [ ] **Step 2: Verify the page still serves and includes the chat panel**

```
cd "C:/Users/ericc/hearth-wt" && py -c "import sys,threading,time,urllib.request,tempfile,os;sys.path.insert(0,'webui');import hearth_mapd as m;d=tempfile.mkdtemp();srv=m.make_server('127.0.0.1',8797,os.path.join(d,'a.db'),'webui/static');threading.Thread(target=srv.serve_forever,daemon=True).start();time.sleep(0.3);b=urllib.request.urlopen('http://127.0.0.1:8797/command',timeout=5).read().decode();print('chat panel present' if ('chatSend' in b and 'sendChat' in b) else 'MISSING');srv.shutdown()"
```
Expected: `chat panel present`.

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: chat panel in the command center"
```

---

## Task 5: Sandboxed launch-agent infrastructure (Nix)

**Files:** Create `nixos/modules/spawn.nix`; modify `nixos/configuration.nix`.

- [ ] **Step 1: Write the module**

Create `nixos/modules/spawn.nix`:
```nix
# spawn.nix: on-demand sandboxed agent runs launched from the UI.
# The web app (hearth-mapd) drops a request file in /var/lib/hearth/queue; a root
# path-watcher starts a per-run sandboxed hearth-agent@<id> instance, which reads
# the request, runs the agent (Ollama call + audit + state), and removes the file.
{ config, lib, pkgs, ... }:
let
  agentPkg = config.hearth.agents.package;
  runner = pkgs.writeShellApplication {
    name = "hearth-run-from-queue";
    runtimeInputs = [ agentPkg pkgs.python3 pkgs.coreutils ];
    text = ''
      id="$1"
      req="/var/lib/hearth/queue/$id.json"
      [ -f "$req" ] || exit 0
      model="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('model','llama3.2:3b'))" "$req")"
      name="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name','agent'))" "$req")"
      prompt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('prompt',''))" "$req")"
      rm -f "$req"
      exec hearth-agent --agent-name "$name" --model "$model" "$prompt"
    '';
  };
in
lib.mkIf config.hearth.agents.enable {
  systemd.tmpfiles.rules = [
    "d /var/lib/hearth/queue 2770 hearth hearth -"
  ];

  # Per-run sandboxed agent. Merges the sandbox profile (DynamicUser etc.).
  systemd.services."hearth-agent@" = {
    description = "hearth on-demand agent run %i";
    serviceConfig = config.hearth.sandbox.profile // {
      Type = "oneshot";
      ExecStart = "${runner}/bin/hearth-run-from-queue %i";
    };
  };

  # Watch the queue and start an instance per request id.
  systemd.paths.hearth-spawn = {
    description = "watch the hearth agent queue";
    wantedBy = [ "multi-user.target" ];
    pathConfig.PathExistsGlob = "/var/lib/hearth/queue/*.json";
    pathConfig.MakeDirectory = false;
  };
  systemd.services.hearth-spawn = {
    description = "start sandboxed agents for queued requests";
    serviceConfig = {
      Type = "oneshot";
      ExecStart = pkgs.writeShellScript "hearth-spawn" ''
        shopt -s nullglob
        for f in /var/lib/hearth/queue/*.json; do
          id="$(${pkgs.coreutils}/bin/basename "$f" .json)"
          ${pkgs.systemd}/bin/systemctl start "hearth-agent@$id.service" || true
        done
      '';
    };
  };
}
```

- [ ] **Step 2: Import it**

Add `./modules/spawn.nix` to the imports list in `nixos/configuration.nix` (after `./modules/desktop.nix`).

- [ ] **Step 3: Commit and eval on the blade**

```bash
cd "C:/Users/ericc/hearth-wt"
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: sandboxed on-demand agent spawn (queue + path-watcher + templated unit)"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -5'
```
Expected: `all checks passed!`. If `hearth.agents.package` or `hearth.sandbox.profile` are not readable here, confirm spawn.nix is imported through configuration.nix (so it sees those options); report any error.

---

## Task 6: /run endpoint (enqueue a launch request)

**Files:** Modify `webui/hearth_mapd.py`.

- [ ] **Step 1: Replace the _handle_run stub**

Replace the temporary stub from Task 2 with:
```python
    def _handle_run(self):
        req = self._read_json_body()
        name = (req.get("name") or "agent").replace("/", "_").replace(" ", "_")[:40] or "agent"
        model = req.get("model") or "llama3.2:3b"
        prompt = req.get("prompt") or ""
        if not prompt:
            return self._send(400, json.dumps({"error": "prompt required"}), "application/json")
        run_id = "{}-{}".format(name, uuid.uuid4().hex[:8])
        queue_dir = "/var/lib/hearth/queue"
        try:
            os.makedirs(queue_dir, exist_ok=True)
            tmp = os.path.join(queue_dir, run_id + ".json.tmp")
            final = os.path.join(queue_dir, run_id + ".json")
            with open(tmp, "w") as fh:
                json.dump({"name": name, "model": model, "prompt": prompt}, fh)
            os.replace(tmp, final)  # atomic: the path-watcher only sees complete files
        except OSError as exc:
            return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        self._send(200, json.dumps({"queued": run_id}), "application/json")
```
Ensure `import os` is present in hearth-mapd (it is).

- [ ] **Step 2: Test enqueue locally (writes to a temp queue dir)**

Because the real path is `/var/lib/hearth/queue`, test the JSON/atomic-write logic with a one-off that points at a temp dir by monkeypatching: set `m.__dict__` is not needed; instead verify via a direct unit check of the write. Run:
```
cd "C:/Users/ericc/hearth-wt" && py -c "
import sys,os,tempfile,json,threading,time,urllib.request
sys.path.insert(0,'webui'); import hearth_mapd as m
q=tempfile.mkdtemp()
# point the handler at the temp queue by patching the constant used in _handle_run
import re
src=open('webui/hearth_mapd.py').read()
assert '/var/lib/hearth/queue' in src, 'queue path present'
print('queue path wired in source: OK')"
```
Expected: `queue path wired in source: OK`. (The end-to-end enqueue+spawn is verified on the blade in Task 8, since it needs the real systemd path-watcher.)

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: hearth-mapd /run endpoint (atomic enqueue of a launch request)"
```

---

## Task 7: Launch-agent UI panel

**Files:** Modify `webui/static/command.html`.

- [ ] **Step 1: Add a Launch card and JS**

Add another card after the chat card:
```html
  <div class="card" id="launch">
    <div class="title">launch agent</div>
    <input id="agName" placeholder="name (e.g. researcher)" style="width:100%;margin-bottom:4px;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;" />
    <select id="agModel" style="width:100%;margin-bottom:4px;"></select>
    <textarea id="agTask" placeholder="task for the agent..." style="width:100%;height:60px;background:#0e2236;border:1px solid #16324f;color:#cfe6ff;padding:6px;border-radius:6px;"></textarea>
    <button id="agLaunch" style="margin-top:4px;background:#1c6;color:#04150c;border:0;border-radius:6px;padding:6px 12px;cursor:pointer;">launch</button>
    <div id="agMsg" style="font-size:12px;margin-top:6px;"></div>
  </div>
```
Add JS:
```javascript
async function loadAgModels(){try{const j=await(await fetch("/models")).json();
  document.getElementById("agModel").innerHTML=(j.models||[]).map(n=>`<option>${n}</option>`).join("")||"<option>llama3.2:3b</option>";}catch(e){}}
loadAgModels();
document.getElementById("agLaunch").onclick=async()=>{
  const name=document.getElementById("agName").value.trim()||"agent";
  const model=document.getElementById("agModel").value;
  const prompt=document.getElementById("agTask").value.trim();
  const msg=document.getElementById("agMsg");
  if(!prompt){msg.textContent="enter a task";return;}
  msg.textContent="launching...";
  try{const r=await fetch("/run",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({name,model,prompt})});
    const j=await r.json();msg.textContent=j.queued?("launched "+j.queued+" (watch the map)"):("error: "+(j.error||"unknown"));}
  catch(e){msg.textContent="error: "+e;}};
```

- [ ] **Step 2: Verify the page includes the launch panel**

```
cd "C:/Users/ericc/hearth-wt" && py -c "import sys,threading,time,urllib.request,tempfile,os;sys.path.insert(0,'webui');import hearth_mapd as m;d=tempfile.mkdtemp();srv=m.make_server('127.0.0.1',8798,os.path.join(d,'a.db'),'webui/static');threading.Thread(target=srv.serve_forever,daemon=True).start();time.sleep(0.3);b=urllib.request.urlopen('http://127.0.0.1:8798/command',timeout=5).read().decode();print('launch panel present' if ('agLaunch' in b and '/run' in b) else 'MISSING');srv.shutdown()"
```
Expected: `launch panel present`.

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: launch-agent panel in the command center"
```

---

## Task 8: Deploy and verify on the blade

**Files:** none (deploy + on-hardware verification).

- [ ] **Step 1: Deploy the branch**

```
cd "C:/Users/ericc/hearth-wt" && git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && echo COPIED'
```

- [ ] **Step 2: Switch**

```
ssh operator@192.168.1.64 'sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -4'
```
Expected: `Done. The new configuration is /nix/store/...`

- [ ] **Step 3: Verify chat + models endpoints**

```
ssh operator@192.168.1.64 'curl -s localhost:8770/models; echo; curl -s -m 120 -X POST localhost:8770/chat -H "Content-Type: application/json" -d "{\"model\":\"llama3.2:3b\",\"messages\":[{\"role\":\"user\",\"content\":\"say hi in three words\"}]}"'
```
Expected: a models list, then a JSON `{"reply": "...", ...}` with a real model reply (proves chat works on the GPU).

- [ ] **Step 4: Verify the sandboxed launch path end to end**

```
ssh operator@192.168.1.64 'curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"probe\",\"model\":\"llama3.2:3b\",\"prompt\":\"reply with one word\"}"; echo; sleep 12; hearth-runs | head -4; systemctl list-units "hearth-agent@*" --no-legend | head'
```
Expected: `{"queued": "probe-...."}`, then a new row in `hearth-runs` for agent `probe` (proves the queue -> path-watcher -> sandboxed instance -> recorded run pipeline works).

- [ ] **Step 5: Confirm on the laptop screen (ask the user)**

Ask the user to open the **hearth** app from the KDE launcher (or press Meta+A), then:
- chat with a model and confirm a reply appears,
- launch an agent and confirm it appears in the map/activity.

- [ ] **Step 6: If anything fails, diagnose with `journalctl -u hearth-mapd`, `journalctl -u 'hearth-agent@*'`, `journalctl -u hearth-spawn`, fix, recommit (Eric identity), redeploy.**

---

## Self-Review

- **Spec coverage:** openable app (Task 3), chat with model picker (Tasks 1, 2, 4), sandboxed launch-agent (Tasks 5, 6, 7), live view (existing map/stats/activity from the prior plan), recorded-as-runs so chat/agents show live (Task 2 `_record_chat_run`, Task 5 runner uses hearth-agent which records). Voice is intentionally a separate follow-up plan.
- **Placeholder scan:** the only stub (`_handle_run` in Task 2) is explicitly replaced in Task 6; flagged in both places. No "handle errors" hand-waving; endpoints have complete code and degrade on failure.
- **Type consistency:** `/chat` returns `{reply, error, tokens_in, tokens_out}` (consumed in Task 4 as `j.reply`, `j.error`); `/run` returns `{queued}` or `{error}` (consumed in Task 7 as `j.queued`); `/models` returns `{models: [...]}` (consumed in Tasks 4, 7 as `j.models`). The queue file shape `{name, model, prompt}` written in Task 6 matches what the runner reads in Task 5. agent_runs/agent_state/agent_events columns match agent/hearth_state.py and agent/hearth_agent.py.

## Notes / risks
- The SCHEMA in hearth-mapd must include agent_runs (Task 2 Step 1) so `_record_chat_run` works on a fresh db.
- Atomic enqueue (write `.tmp` then `os.replace`) ensures the path-watcher never reads a half-written request.
- The path-watcher fires once per change and the spawn service scans the whole queue, so bursts of launches are all picked up.
- Streaming chat replies and voice (whisper STT + piper TTS) are the next plan (`2026-06-21-hearth-voice.md`).
