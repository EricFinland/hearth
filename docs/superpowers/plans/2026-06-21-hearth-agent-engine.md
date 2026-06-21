# hearth agent engine (tool-using loop + coding/http tools) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn launched agents from one-shot prompts into a real tool-using loop: the model is given a goal and tools (run a command, read/write files, call an HTTP API) and works step by step in a sandboxed workspace until the task is done.

**Architecture:** A pluggable tool registry (`agent/hearth_tools.py`) and an agent loop (`agent/hearth_loop.py`) that calls Ollama's chat API with tool specs, executes returned tool calls in a per-run workspace, feeds results back, and repeats under an iteration cap. The existing sandboxed spawn path runs `hearth-loop` instead of the one-shot runner, in a per-run workspace under `/var/lib/hearth/agents/<run>/`. Adding a capability = registering a tool.

**Tech Stack:** Python 3 stdlib (subprocess, urllib, json), Ollama chat tool-calling, the existing hearth sandbox (DynamicUser systemd unit) + audit/state (`agent/hearth_state.py`).

**Repo conventions (read first):**
- Work in the worktree `C:\Users\ericc\hearth-wt` (branch `worktree-desktop`). Do NOT touch `C:\Users\ericc\OneDrive\Desktop\hearth`.
- Commit as Eric: `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit ...`. No AI attribution. No em dashes in committed files.
- Python tested locally with `py` (3.14, stdlib only). Nix eval/build on the blade: `ssh operator@192.168.1.64` (key auth, passwordless sudo, flakes). WiFi intermittent; retry on timeout. Deploy by `git archive -o file.tar HEAD` then `scp` the tar and extract into `~/hearth-desktop` (do NOT use a long `git archive | ssh tar` pipe; it corrupts on WiFi blips).
- Existing: `agent/hearth_state.py` has `emit_state(agent_id, state, detail, db=...)` and `STATES`/`STATE_ICONS`. `agent/hearth_agent.py` is the one-shot runner (records a run to `agent_runs`). The agent source dir is packaged as a directory in `nixos/modules/agents.nix` (so modules in `agent/` can import each other). `nixos/modules/spawn.nix` has a `runner` (`hearth-run-from-queue`) that currently `exec`s `hearth-agent`; agents run under `config.hearth.sandbox.profile` (DynamicUser, ReadWritePaths agents+runs+queue).

---

## File Structure

- Create `agent/hearth_tools.py` - tool registry + coding tools (run_command, read_file, write_file, list_files) + http_request tool. Pure functions taking `(args, workspace)`.
- Create `agent/hearth_loop.py` - the agent loop (Ollama chat tool-calling) + CLI + `--self-test`.
- Modify `nixos/modules/agents.nix` - package a `hearth-loop` command; add a dev toolchain (git, gcc, gnumake) to the agent environment.
- Modify `nixos/modules/spawn.nix` - the runner makes a per-run workspace and execs `hearth-loop` instead of `hearth-agent`.

---

## Task 1: tool registry + file/command tools

**Files:** Create `agent/hearth_tools.py`.

- [ ] **Step 1: Write the module with the registry and tools**

```python
#!/usr/bin/env python3
"""hearth agent tools: a small pluggable registry. A tool is a dict with a name,
a description, a JSON-schema for its parameters, and a `fn(args, workspace)` that
runs it and returns a short string result. Adding a capability means adding a
tool here (or registering one at runtime). Standard library only.

All file/command tools operate inside the per-run workspace and refuse paths that
escape it, as defence in depth on top of the systemd sandbox.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

COMMAND_TIMEOUT = 120
HTTP_TIMEOUT = 30
MAX_OUT = 4000


def _safe_join(workspace, path):
    path = (path or "").lstrip("/")
    full = os.path.realpath(os.path.join(workspace, path))
    root = os.path.realpath(workspace)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path escapes workspace: {}".format(path))
    return full


def tool_run_command(args, workspace):
    cmd = args.get("command", "")
    if not cmd:
        return "error: no command"
    try:
        r = subprocess.run(cmd, shell=True, cwd=workspace, capture_output=True,
                           text=True, timeout=COMMAND_TIMEOUT)
        out = (r.stdout or "")[-MAX_OUT:]
        err = (r.stderr or "")[-2000:]
        return "exit={}\nstdout:\n{}\nstderr:\n{}".format(r.returncode, out, err)
    except subprocess.TimeoutExpired:
        return "error: command timed out after {}s".format(COMMAND_TIMEOUT)
    except OSError as exc:
        return "error: {}".format(exc)


def tool_write_file(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path"))
    except ValueError as exc:
        return "error: {}".format(exc)
    content = args.get("content", "")
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)
    return "wrote {} ({} bytes)".format(args.get("path"), len(content))


def tool_read_file(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path"))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        with open(full) as fh:
            return fh.read()[:MAX_OUT]
    except OSError as exc:
        return "error: {}".format(exc)


def tool_list_files(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        return "\n".join(sorted(os.listdir(full))) or "(empty)"
    except OSError as exc:
        return "error: {}".format(exc)


def tool_http_request(args, workspace):
    url = args.get("url")
    if not url:
        return "error: no url"
    method = (args.get("method") or "GET").upper()
    headers = args.get("headers") or {}
    body = args.get("body")
    data = body.encode() if isinstance(body, str) else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")[:MAX_OUT]
            return "status={}\n{}".format(resp.status, text)
    except urllib.error.HTTPError as exc:
        return "status={}\n{}".format(exc.code, exc.read().decode("utf-8", "replace")[:2000])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)


TOOLS = [
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace. Use for building, testing, and inspecting code.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "the shell command"}},
            "required": ["command"]},
        "fn": tool_run_command,
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a file in the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
        "fn": tool_write_file,
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
        "fn": tool_read_file,
    },
    {
        "name": "list_files",
        "description": "List files in a workspace directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "fn": tool_list_files,
    },
    {
        "name": "http_request",
        "description": "Make an HTTP request to an external API. Provide url, optional method, headers, body.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "method": {"type": "string"},
            "headers": {"type": "object"}, "body": {"type": "string"}},
            "required": ["url"]},
        "fn": tool_http_request,
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def ollama_tool_specs():
    """The tools in Ollama's chat tool format."""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in TOOLS]


def execute_tool(name, args, workspace):
    tool = _BY_NAME.get(name)
    if tool is None:
        return "error: unknown tool {}".format(name)
    try:
        return tool["fn"](args or {}, workspace)
    except Exception as exc:  # noqa: BLE001 - a tool error must not crash the loop
        return "error: {}: {}".format(type(exc).__name__, exc)


def _self_test():
    import tempfile
    ws = tempfile.mkdtemp(prefix="hearth-tools-")
    assert "wrote" in execute_tool("write_file", {"path": "a/b.txt", "content": "hi"}, ws)
    assert execute_tool("read_file", {"path": "a/b.txt"}, ws) == "hi"
    assert "b.txt" in execute_tool("list_files", {"path": "a"}, ws)
    out = execute_tool("run_command", {"command": "echo hello"}, ws)
    assert "hello" in out and "exit=0" in out, out
    assert "escapes workspace" in execute_tool("write_file", {"path": "../evil", "content": "x"}, ws)
    assert len(ollama_tool_specs()) == len(TOOLS)
    print("hearth-tools self-test OK")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
```

- [ ] **Step 2: Run the self-test**

Run: `cd "C:/Users/ericc/hearth-wt" && py agent/hearth_tools.py`
Expected: `hearth-tools self-test OK`

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_tools.py && \
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: pluggable agent tool registry (command, file, http tools)"
```

---

## Task 2: the agent loop

**Files:** Create `agent/hearth_loop.py`.

- [ ] **Step 1: Write the loop**

```python
#!/usr/bin/env python3
"""hearth agent loop: give the model a goal and tools; it thinks, calls tools,
reads results, and repeats until done (or hits the iteration cap). Uses Ollama's
chat tool-calling. Emits runtime state per step (for the live map) and records
the run. Standard library only.

Usage:
  hearth-loop --model qwen2.5-coder --agent-name builder --workspace DIR "GOAL"
  hearth-loop --self-test    # runs the loop against a mock model, no Ollama
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_tools  # noqa: E402
try:
    import hearth_state  # noqa: E402
except Exception:  # noqa: BLE001
    hearth_state = None

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MAX_ITERS = 12

SYSTEM_PROMPT = (
    "You are a capable agent working in a sandboxed workspace. You have tools to "
    "run shell commands, read and write files, and make HTTP requests. Use them to "
    "accomplish the goal step by step. When the goal is complete, reply with a short "
    "summary and do not call any more tools."
)


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _emit(agent_id, state, detail, db):
    if hearth_state is not None:
        try:
            hearth_state.emit_state(agent_id, state, detail, db=db)
        except Exception:  # noqa: BLE001
            pass


def chat(base_url, model, messages, tools, timeout=300):
    """One Ollama chat call with tools. Returns the assistant message dict."""
    body = json.dumps({"model": model, "messages": messages, "tools": tools,
                       "stream": False}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    return data.get("message") or {}, int(data.get("eval_count", 0) or 0)


def run_loop(goal, model, workspace, db=DEFAULT_DB, agent_name="agent",
             ollama_url=DEFAULT_OLLAMA, max_iters=MAX_ITERS, chat_fn=None):
    """Drive the agent loop. chat_fn is injectable for testing."""
    chat_fn = chat_fn or (lambda msgs: chat(ollama_url, model, msgs, hearth_tools.ollama_tool_specs()))
    os.makedirs(workspace, exist_ok=True)
    messages = [{"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": goal}]
    _emit(agent_name, "SPAWNING", "starting", db)
    tokens_out = 0
    error = None
    final = ""
    t0 = time.monotonic()
    try:
        for _ in range(max_iters):
            _emit(agent_name, "THINKING", "calling " + model, db)
            msg, tout = chat_fn(messages)
            tokens_out += tout
            messages.append(msg)
            calls = msg.get("tool_calls") or []
            if not calls:
                final = msg.get("content", "")
                break
            for call in calls:
                fn = call.get("function") or {}
                name = fn.get("name", "")
                raw = fn.get("arguments")
                cargs = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
                _emit(agent_name, "TOOL_CALL", name, db)
                result = hearth_tools.execute_tool(name, cargs, workspace)
                messages.append({"role": "tool", "content": result[:4000]})
        else:
            error = "hit iteration cap ({})".format(max_iters)
    except Exception as exc:  # noqa: BLE001
        error = "{}: {}".format(type(exc).__name__, exc)

    latency_ms = int((time.monotonic() - t0) * 1000)
    _record(db, agent_name, model, tokens_out, latency_ms, error)
    _emit(agent_name, "ERRORED" if error else "DONE", error or "task complete", db)
    return final, error


def _record(db, agent_name, model, tokens_out, latency_ms, error):
    run_id = uuid.uuid4().hex
    ts = _now_iso()
    schema = getattr(hearth_state, "SCHEMA", "")
    try:
        con = sqlite3.connect(db, timeout=10)
        if schema:
            con.executescript(schema)
        con.execute(
            "CREATE TABLE IF NOT EXISTS agent_runs (id INTEGER PRIMARY KEY, "
            "agent_name TEXT, run_id TEXT, started_at TEXT, finished_at TEXT, "
            "tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER, "
            "error TEXT, model TEXT)")
        con.execute(
            "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
            "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (agent_name, run_id, ts, ts, 0, tokens_out, 0.0, latency_ms, error, model))
        con.commit()
        con.close()
    except sqlite3.Error:
        pass


def _self_test():
    import tempfile
    ws = tempfile.mkdtemp(prefix="hearth-loop-")
    db = os.path.join(ws, "audit.db")
    steps = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "write_file",
            "arguments": {"path": "hi.txt", "content": "hello world"}}}]}, 3),
        ({"role": "assistant", "content": "done, wrote hi.txt"}, 2),
    ]
    seq = iter(steps)
    final, error = run_loop("write hi.txt", "mock", ws, db=db, agent_name="t",
                            chat_fn=lambda msgs: next(seq))
    assert error is None, error
    assert final == "done, wrote hi.txt", final
    with open(os.path.join(ws, "hi.txt")) as fh:
        assert fh.read() == "hello world"
    print("hearth-loop self-test OK:", final)
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-loop")
    p.add_argument("goal", nargs="?")
    p.add_argument("--model", default="qwen2.5-coder")
    p.add_argument("--agent-name", default="agent")
    p.add_argument("--workspace", default=".")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    p.add_argument("--max-iters", type=int, default=MAX_ITERS)
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if not a.goal:
        p.error("a goal is required unless --self-test")
    final, error = run_loop(a.goal, a.model, a.workspace, db=a.db,
                            agent_name=a.agent_name, ollama_url=a.ollama_url,
                            max_iters=a.max_iters)
    if error:
        print("hearth-loop error:", error, file=sys.stderr)
        return 1
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 2: Run the self-test (mock model, real tools)**

Run: `cd "C:/Users/ericc/hearth-wt" && py agent/hearth_loop.py --self-test`
Expected: `hearth-loop self-test OK: done, wrote hi.txt`

- [ ] **Step 3: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_loop.py && \
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: tool-using agent loop (Ollama tool-calling, sandbox-aware, audited)"
```

---

## Task 3: package hearth-loop + add the dev toolchain

**Files:** Modify `nixos/modules/agents.nix`.

- [ ] **Step 1: Add the hearth-loop package and toolchain**

Read `nixos/modules/agents.nix`. It already defines `agentSrc = ../../agent;` and packages `hearthAgent`/`hearthState` via `writeShellApplication`. Add a `hearthLoop` package the same way:
```nix
  hearthLoop = pkgs.writeShellApplication {
    name = "hearth-loop";
    runtimeInputs = [ pkgs.python3 ];
    text = ''
      exec ${pkgs.python3}/bin/python3 ${agentSrc}/hearth_loop.py "$@"
    '';
  };
```
Add `hearthLoop` to the `environment.systemPackages` list (next to `hearthAgent`). In the same `environment.systemPackages` (with pkgs;) list, add the dev toolchain so sandboxed agents can build code: `git`, `gcc`, `gnumake`. (python3, uv, nodejs_22 are already there.)

- [ ] **Step 2: Commit and eval on the blade**

```bash
cd "C:/Users/ericc/hearth-wt"
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: package hearth-loop and add a dev toolchain (git, gcc, make) for agents"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -4'
```
Expected: `all checks passed!`

---

## Task 4: run the loop in the sandbox with a per-run workspace

**Files:** Modify `nixos/modules/spawn.nix`.

- [ ] **Step 1: Update the runner to make a workspace and exec hearth-loop**

Read `nixos/modules/spawn.nix`. In the `runner` writeShellApplication, change `runtimeInputs` to include the loop instead of (or in addition to) the agent, and change the final `exec` line. Replace the runner `text` body so that after parsing the request it creates a per-run workspace and runs the loop:
```bash
      id="$1"
      req="/var/lib/hearth/queue/$id.json"
      [ -f "$req" ] || exit 0
      model="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('model','qwen2.5-coder'))" "$req")"
      name="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('name','agent'))" "$req")"
      prompt="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1])).get('prompt',''))" "$req")"
      rm -f "$req"
      ws="/var/lib/hearth/agents/$id"
      mkdir -p "$ws"
      exec hearth-loop --agent-name "$name" --model "$model" --workspace "$ws" "$prompt"
```
And set `runtimeInputs = [ agentPkg config.hearth.agents.loopPackage pkgs.python3 pkgs.coreutils ];` - OR simpler, since hearth-loop is on the system PATH via Task 3, keep `runtimeInputs = [ agentPkg pkgs.python3 pkgs.coreutils ]` and rely on the system PATH for `hearth-loop`. To be robust, expose the loop package: in `agents.nix` add an internal option `hearth.agents.loopPackage` set to `hearthLoop` (mirroring how `hearth.agents.package` exposes `hearthAgent`), then reference `config.hearth.agents.loopPackage` in spawn.nix runtimeInputs and call its binary by absolute path: `exec ${config.hearth.agents.loopPackage}/bin/hearth-loop ...`.

Concretely: in `agents.nix` add to the options block `loopPackage = lib.mkOption { type = lib.types.package; internal = true; description = "the hearth-loop runner"; };` and in config set `hearth.agents.loopPackage = hearthLoop;`. Then in `spawn.nix`, use `exec ${config.hearth.agents.loopPackage}/bin/hearth-loop --agent-name "$name" --model "$model" --workspace "$ws" "$prompt"` and add `config.hearth.agents.loopPackage` to the runner runtimeInputs.

- [ ] **Step 2: Commit and eval on the blade**

```bash
cd "C:/Users/ericc/hearth-wt"
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -am "feat: sandboxed launches run the tool-using loop in a per-run workspace"
git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && nix flake check --no-build 2>&1 | tail -5'
```
Expected: `all checks passed!`

---

## Task 5: deploy, pull a coding model, and verify a real agent run

**Files:** none (deploy + on-hardware verification).

- [ ] **Step 1: Deploy and switch**

```
cd "C:/Users/ericc/hearth-wt" && git archive -o "C:/Users/ericc/AppData/Local/Temp/wt.tar" HEAD
scp "C:/Users/ericc/AppData/Local/Temp/wt.tar" operator@192.168.1.64:~/wt.tar
ssh operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service 2>/dev/null; cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -3'
```
Expected: `Done. The new configuration is /nix/store/...`

- [ ] **Step 2: Pull a coding-capable model**

```
ssh operator@192.168.1.64 'ollama pull qwen2.5-coder 2>&1 | tail -2'
```
Expected: the model pulls (a few GB). (Small base models are weak at tool-use; qwen2.5-coder is the recommended local coding model.)

- [ ] **Step 3: Launch a real coding agent and watch it use tools**

```
ssh operator@192.168.1.64 'curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"builder\",\"model\":\"qwen2.5-coder\",\"prompt\":\"Create a Python file hello.py that prints Hello from hearth, then run it with python3 and confirm the output.\"}"; echo; echo waiting...; sleep 90; echo "=== run recorded? ==="; hearth-runs | head -4; echo "=== workspace contents ==="; sudo ls -la /var/lib/hearth/agents/builder-*/ 2>/dev/null | head; echo "=== state ==="; curl -s localhost:8770/state'
```
Expected: a `builder` run in `hearth-runs`, a `hello.py` in the workspace dir, and `builder` shown in `/state`. (The agent should have used `write_file` then `run_command`.)

- [ ] **Step 4: Inspect the agent's steps (journal of the sandboxed instance)**

```
ssh operator@192.168.1.64 'journalctl -u "hearth-agent@builder-*" --no-pager -n 30 2>&1 | tail -30'
```
Expected: state transitions / tool activity for the run. If the model did not call tools well, that is a model-quality issue (try a larger qwen2.5-coder tag); the loop, tools, and sandbox are what this plan delivers.

- [ ] **Step 5: Confirm in the cockpit (ask the user)**

Ask the user to open the hearth app, launch an agent with a coding goal, and watch it work the task in the map/activity.

---

## Self-Review

- **Spec coverage:** tool-using loop (Task 2), pluggable tool registry (Task 1), coding tools run_command/read/write/list (Task 1), http_request outbound-API tool (Task 1), sandboxed per-run workspace (Task 4), dev toolchain for building code (Task 3), wired into the existing sandboxed spawn + audit/map (Tasks 4, 2). Secure credential storage and the inbound control API are the next plan (`hearth-integration`).
- **Placeholder scan:** no TBD/stub; every tool and the loop have complete code; tests are concrete with assertions.
- **Type consistency:** `execute_tool(name, args, workspace)` and `ollama_tool_specs()` defined in Task 1 are used verbatim in the loop (Task 2). `run_loop(goal, model, workspace, db=, agent_name=, ollama_url=, max_iters=, chat_fn=)` signature is used by both the CLI and the self-test. The loop appends Ollama-shaped messages (`role: tool`) and reads `msg.tool_calls[].function.{name,arguments}`, matching Ollama's chat tool-calling. The queue request shape `{name, model, prompt}` (from the app plan) is what the spawn runner reads in Task 4.

## Notes / risks
- Small local models (3B/7B base) are unreliable at tool-calling; qwen2.5-coder is the recommended local model for this (Task 5 pulls it). The engine works regardless of model.
- `arguments` from Ollama may be a dict or a JSON string; the loop handles both.
- The iteration cap (12) and per-tool timeouts bound runaway loops. Tools run inside the DynamicUser sandbox; `_safe_join` keeps file tools inside the per-run workspace as defence in depth.
- Agents have network (needed for http_request); per-agent network policy and secure credential injection are in the next plan.
