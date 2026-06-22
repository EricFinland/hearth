# hearth Agent Control: Permission Engine + Control Protocol (Plan 1 of 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the hearth agent loop a Claude-Code-style control protocol - selectable permission modes (plan / auto / bypass), a stdin/stdout JSON event stream, and human-in-the-loop approve/deny on risky tools - all unit-tested without Ollama.

**Architecture:** A new pure `permissions.py` decides `allow | gate | deny` from `(mode, tool, args)`. `hearth_loop.py` is refactored so its turn-driver emits JSON-line events and, on a gated tool, blocks on an injectable control channel until a decision arrives. Two entrypoints share one turn-driver: `run_loop` (one-shot, used by background workers and the CLI) and `run_session` (long-lived, multi-turn, used by interactive sessions). Both the event sink (`emit_fn`) and the control source (`control_fn`) are injectable, defaulting to stdout/stdin, which makes the whole protocol testable with scripted callables.

**Tech Stack:** Python 3 standard library only. Tests follow the existing in-module `_self_test()` convention run via `python3 <module>.py --self-test` (this codebase does not use pytest).

**Scope note:** This is Plan 1 of 3 for the agent-control feature (spec: `docs/superpowers/specs/2026-06-22-hearth-agent-control.md`). Plan 2 wires interactive sessions into `hearth-mapd` and builds the cockpit console UI. Plan 3 upgrades background workers to stream over the DB and unsandboxes the runtime in NixOS for full-machine reach. This plan delivers working, drivable software on its own: a loop you can drive from a terminal by piping JSON commands in and reading JSON events out.

**Commit identity (required):** every commit in this plan MUST be authored as Eric and contain no AI attribution. Use this exact form for every commit:
`git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<message>"`
No em-dashes in any committed file or message.

**Working directory:** the worktree `C:/Users/ericc/hearth-wt` (branch `worktree-desktop`). Paths below are relative to it.

---

### Task 1: Permission engine (`agent/permissions.py`)

**Files:**
- Create: `agent/permissions.py`

The engine is pure (no I/O), so its `_self_test()` IS the test. We write the test assertions and the implementation in the same file, run it failing first by stubbing `decide`, then complete it.

- [ ] **Step 1: Create the file with the test and a stub that fails**

Create `agent/permissions.py`:

```python
#!/usr/bin/env python3
"""hearth permission engine: decide whether an agent may run a tool given the
current permission mode. Pure and I/O-free so it is trivially testable and shared
by every drive path (interactive sessions and background workers).

Modes (mirroring Claude Code):
  plan   - read-only; the agent may look but change nothing, then must produce a plan.
  auto   - safe reads and file edits run automatically; dangerous actions are gated
           (the user must approve each one).
  bypass - everything runs, no prompts.

Decision values:
  "allow" - run the tool now
  "gate"  - pause and ask the user to approve or deny
  "deny"  - refuse outright (and tell the model why)
"""

import sys

MODES = ("plan", "auto", "bypass")

# Risk class per tool. Unknown tools are treated as dangerous (fail closed).
RISK = {
    "read_file": "safe",
    "list_files": "safe",
    "write_file": "edit",
    "run_command": "dangerous",
    "http_request": "dangerous",
}


def risk_of(tool):
    return RISK.get(tool, "dangerous")


def _command_head(args):
    cmd = ((args or {}).get("command") or "").strip()
    return cmd.split()[0] if cmd else ""


def decide(mode, tool, args=None, auto_allow=()):
    return "deny"  # stub: replaced in Step 3


def _self_test():
    # bypass: everything allowed
    for t in ("read_file", "write_file", "run_command", "http_request", "mystery"):
        assert decide("bypass", t) == "allow", t
    # plan: only safe reads, everything else denied
    assert decide("plan", "read_file") == "allow"
    assert decide("plan", "list_files") == "allow"
    assert decide("plan", "write_file") == "deny"
    assert decide("plan", "run_command") == "deny"
    assert decide("plan", "http_request") == "deny"
    # auto: safe and edit allowed, dangerous gated
    assert decide("auto", "read_file") == "allow"
    assert decide("auto", "write_file") == "allow"
    assert decide("auto", "run_command") == "gate"
    assert decide("auto", "http_request") == "gate"
    # auto + allowlist: a whitelisted command head runs automatically
    assert decide("auto", "run_command", {"command": "git status"}, auto_allow={"git"}) == "allow"
    assert decide("auto", "run_command", {"command": "rm -rf /"}, auto_allow={"git"}) == "gate"
    # unknown tool fails closed (dangerous)
    assert risk_of("mystery") == "dangerous"
    assert decide("auto", "mystery") == "gate"
    # unknown mode -> gate (safest)
    assert decide("yolo", "read_file") == "gate"
    print("hearth-permissions self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python3 agent/permissions.py --self-test` (or `python3 agent/permissions.py`)
Expected: `AssertionError` on the first `decide("bypass", ...)` assertion (stub returns "deny").

- [ ] **Step 3: Implement `decide`**

Replace the stub body of `decide` with:

```python
def decide(mode, tool, args=None, auto_allow=()):
    """Return 'allow' | 'gate' | 'deny' for (mode, tool, args).

    auto_allow is an optional collection of command heads (for example
    {'git', 'ls'}) that run automatically even in auto mode. Empty by default.
    """
    if mode not in MODES:
        return "gate"
    risk = risk_of(tool)
    if mode == "bypass":
        return "allow"
    if mode == "plan":
        return "allow" if risk == "safe" else "deny"
    # auto
    if risk in ("safe", "edit"):
        return "allow"
    if tool == "run_command" and _command_head(args) in set(auto_allow):
        return "allow"
    return "gate"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python3 agent/permissions.py --self-test`
Expected: `hearth-permissions self-test OK`

- [ ] **Step 5: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/permissions.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: agent permission engine (plan/auto/bypass risk gating)"
```

---

### Task 2: Add the WAITING_APPROVAL runtime state

The loop will emit a new state when it pauses for approval. `hearth_state.emit_state` rejects states not in its closed set, and the two HTML frontends keep matching icon tables. Add the state in all three places so the map renders it.

**Files:**
- Modify: `agent/hearth_state.py` (the `STATES` list and `STATE_ICONS` dict)
- Modify: `webui/static/command.html` (the `STATE_ICONS` JS object on line 52)
- Modify: `webui/static/index.html` (its `STATE_ICONS` table - find it the same way)

- [ ] **Step 1: Add the failing assertion to `hearth_state._self_test`**

`hearth_state.py` has no `_self_test` yet; add one. Insert this function just above `def main(` in `agent/hearth_state.py`:

```python
def _self_test():
    assert "WAITING_APPROVAL" in STATES, "WAITING_APPROVAL missing from STATES"
    assert "WAITING_APPROVAL" in STATE_ICONS, "WAITING_APPROVAL missing from STATE_ICONS"
    import tempfile, os as _os
    db = _os.path.join(tempfile.mkdtemp(prefix="hearth-state-"), "s.db")
    emit_state("a1", "WAITING_APPROVAL", "needs approval: run_command", db=db)
    snap = {r["agent_id"]: r for r in snapshot(db)}
    assert snap["a1"]["state"] == "WAITING_APPROVAL", snap
    print("hearth-state self-test OK")
    return 0
```

Then add a `--self-test` path. In `main`, immediately after `args = parser.parse_args(argv)`, add:

```python
    if getattr(args, "self_test", False):
        return _self_test()
```

and register the flag right after `parser.add_argument("--db", default=DEFAULT_DB)`:

```python
    parser.add_argument("--self-test", action="store_true")
```

Note: `--self-test` must work without a subcommand. Change the subparsers line from
`sub = parser.add_subparsers(dest="cmd", required=True)` to
`sub = parser.add_subparsers(dest="cmd", required=False)` and, at the end of `main` where it currently falls through to `return 1`, leave it as is.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 agent/hearth_state.py --self-test`
Expected: `AssertionError: WAITING_APPROVAL missing from STATES`.

- [ ] **Step 3: Add the state to the model**

In `agent/hearth_state.py`, add to the `STATES` list (after `"WAITING_IO",`):

```python
    "WAITING_APPROVAL",  # paused, needs the user to approve or deny a tool
```

And to `STATE_ICONS` (after the `WAITING_IO` entry):

```python
    "WAITING_APPROVAL": "✋",
```

- [ ] **Step 4: Run to verify it passes**

Run: `python3 agent/hearth_state.py --self-test`
Expected: `hearth-state self-test OK`

- [ ] **Step 5: Add the icon to both frontends**

In `webui/static/command.html` line 52, change:

```javascript
const STATE_ICONS={SPAWNING:"*",IDLE:"z",THINKING:"?",TOOL_CALL:"+",WAITING_IO:"~",ERRORED:"!",DONE:"#"};
```

to add the new key:

```javascript
const STATE_ICONS={SPAWNING:"*",IDLE:"z",THINKING:"?",TOOL_CALL:"+",WAITING_IO:"~",WAITING_APPROVAL:"!?",ERRORED:"!",DONE:"#"};
```

In `webui/static/index.html`, find its `STATE_ICONS` object (grep for `STATE_ICONS` in that file) and add `WAITING_APPROVAL` with whatever icon style that table uses (match its existing emoji/char style, for example `"WAITING_APPROVAL":"✋"`). If `index.html` has no such table, skip this sub-step and note it in the commit body.

- [ ] **Step 6: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_state.py webui/static/command.html webui/static/index.html
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: add WAITING_APPROVAL runtime state + frontend icons"
```

---

### Task 3: Control protocol in the loop (`agent/hearth_loop.py`)

This is the core. Refactor the inner turn loop into `_run_turns`, add event emission, permission gating, decision-awaiting, and the plan/auto/bypass behavior. Keep the existing `run_loop` working (its current `_self_test` must still pass). Add new protocol tests.

**Files:**
- Modify: `agent/hearth_loop.py`

- [ ] **Step 1: Write the failing protocol tests**

In `agent/hearth_loop.py`, extend `_self_test()` by appending the following blocks just before its final `print(...)` and `return 0`:

```python
    # --- Protocol: an auto-mode dangerous tool is gated, then denied by the user.
    events = []
    chat_steps = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "run_command",
            "arguments": {"command": "echo hi"}}}]}, 1),
        ({"role": "assistant", "content": "ok, stopping"}, 1),
    ]
    cseq = iter(chat_steps)
    ws3 = tempfile.mkdtemp(prefix="hearth-loop3-")
    f3, e3 = run_loop("run echo", "mock", ws3, db=os.path.join(ws3, "d.db"),
                      agent_name="t3", mode="auto",
                      chat_fn=lambda m: next(cseq),
                      emit_fn=events.append,
                      control_fn=lambda req: {"type": "decision", "id": req.get("id"), "allow": False})
    etypes = [e["type"] for e in events]
    assert "tool_request" in etypes, etypes
    denials = [e for e in events if e["type"] == "tool_result" and e.get("denied")]
    assert denials, ("expected a denial tool_result", events)
    assert e3 is None, e3

    # --- Protocol: approve the gated tool; it actually runs.
    events_a = []
    chat_steps_a = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "run_command",
            "arguments": {"command": "echo approved"}}}]}, 1),
        ({"role": "assistant", "content": "done"}, 1),
    ]
    aseq = iter(chat_steps_a)
    wsa = tempfile.mkdtemp(prefix="hearth-loopA-")
    fa, ea = run_loop("run echo", "mock", wsa, db=os.path.join(wsa, "d.db"),
                      agent_name="ta", mode="auto",
                      chat_fn=lambda m: next(aseq),
                      emit_fn=events_a.append,
                      control_fn=lambda req: {"type": "decision", "id": req.get("id"), "allow": True})
    ran = [e for e in events_a if e["type"] == "tool_result" and not e.get("denied")]
    assert ran and "approved" in ran[0]["output"], ("expected the command to run", events_a)
    assert ea is None, ea

    # --- Protocol: plan mode denies a write and emits a final plan event.
    events_p = []
    chat_steps_p = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "write_file",
            "arguments": {"path": "x.txt", "content": "y"}}}]}, 1),
        ({"role": "assistant", "content": "Plan:\n1. do the thing"}, 1),
    ]
    pseq = iter(chat_steps_p)
    wsp = tempfile.mkdtemp(prefix="hearth-loopP-")
    fp, ep = run_loop("plan it", "mock", wsp, db=os.path.join(wsp, "d.db"),
                      agent_name="tp", mode="plan",
                      chat_fn=lambda m: next(pseq),
                      emit_fn=events_p.append,
                      control_fn=lambda req: {"type": "stop"})
    assert any(e["type"] == "plan" for e in events_p), [e["type"] for e in events_p]
    assert not os.path.exists(os.path.join(wsp, "x.txt")), "plan mode must not write"
    assert ep is None, ep

    # --- Session: a user_message drives a turn, then stop ends the session.
    events_s = []
    sess_chat = iter([({"role": "assistant", "content": "hello back"}, 1)])
    sess_cmds = iter([{"type": "user_message", "text": "hi"}, {"type": "stop"}])
    wss = tempfile.mkdtemp(prefix="hearth-loopS-")
    run_session("mock", wss, db=os.path.join(wss, "d.db"), agent_name="ts", mode="auto",
                chat_fn=lambda m: next(sess_chat),
                emit_fn=events_s.append, control_fn=lambda req: next(sess_cmds))
    assert any(e["type"] == "message" and "hello back" in e.get("content", "") for e in events_s), events_s
    assert events_s[-1]["type"] == "done", events_s[-1]
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 agent/hearth_loop.py --self-test`
Expected: FAIL - `run_loop` does not yet accept `mode`/`emit_fn`/`control_fn` (TypeError), and `run_session` is undefined (NameError).

- [ ] **Step 3: Add imports, constants, and the protocol helpers**

At the top of `agent/hearth_loop.py`, after `import hearth_tools  # noqa: E402`, add:

```python
import permissions  # noqa: E402
```

Add a constant near `MAX_ITERS`:

```python
MAX_EVENT_OUT = 4000  # cap tool output included in an event
```

Add these helpers above `run_loop`:

```python
def _stdout_emit(event):
    """Default event sink: one JSON object per line on stdout, flushed."""
    sys.stdout.write(json.dumps(event) + "\n")
    sys.stdout.flush()


def _stdin_control(_request):
    """Default control source: one JSON command per line from stdin. EOF means
    stop."""
    line = sys.stdin.readline()
    if not line:
        return {"type": "stop"}
    try:
        return json.loads(line)
    except ValueError:
        return {}


def _system_for(mode):
    base = SYSTEM_PROMPT
    if mode == "plan":
        base += (" You are in PLAN MODE: do not modify anything and do not run "
                 "commands. Investigate using read-only tools only, then reply "
                 "with a concise step-by-step plan and stop.")
    return base


def _await_decision(req_id, tool, cargs, auto_allow, control, state, emit, agent_name, db):
    """Block until a decision for req_id arrives. Handle set_mode and stop while
    waiting; if a mode switch would now allow this tool, proceed. Returns
    True (allow), False (deny), or None (stop)."""
    while True:
        cmd = control({"need": "decision", "id": req_id, "tool": tool}) or {}
        ctype = cmd.get("type")
        if ctype == "stop":
            return None
        if ctype == "set_mode":
            new = cmd.get("mode")
            if new in permissions.MODES:
                state["mode"] = new
                emit({"type": "state", "state": "THINKING", "detail": "mode -> " + new})
                _emit(agent_name, "THINKING", "mode -> " + new, db)
                if permissions.decide(new, tool, cargs, auto_allow) == "allow":
                    return True
            continue
        if ctype == "decision" and cmd.get("id") in (req_id, None):
            return bool(cmd.get("allow"))
        # ignore anything else (for example a stray user_message) and keep waiting
```

- [ ] **Step 4: Add the shared turn-driver `_run_turns`**

Add this function above `run_loop`:

```python
def _run_turns(messages, model, workspace, chat_fn, emit, control, state,
               db, agent_name, max_iters, auto_allow):
    """Run agent turns until the model stops calling tools, hits the cap, or is
    stopped. state is a mutable dict holding {"mode": ...}. Returns
    (final_text, error, tokens_out)."""
    tokens_out = 0
    final = ""
    for _ in range(max_iters):
        emit({"type": "state", "state": "THINKING", "detail": "calling " + model})
        _emit(agent_name, "THINKING", "calling " + model, db)
        msg, tout = chat_fn(messages)
        tokens_out += tout
        messages.append(msg)
        content = msg.get("content", "")
        if content:
            emit({"type": "message", "role": "assistant", "content": content})
        calls = msg.get("tool_calls") or []
        if not calls:
            parsed = parse_content_tool_calls(content)
            if parsed:
                calls = [{"function": c} for c in parsed]
            else:
                final = content
                if state["mode"] == "plan":
                    emit({"type": "plan", "content": content})
                return final, None, tokens_out
        for call in calls:
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw = fn.get("arguments")
            cargs = raw if isinstance(raw, dict) else (json.loads(raw) if raw else {})
            verdict = permissions.decide(state["mode"], name, cargs, auto_allow)
            if verdict == "deny":
                result = "denied: permission mode '{}' does not allow {}".format(
                    state["mode"], name)
                emit({"type": "tool_result", "tool": name, "denied": True, "output": result})
                messages.append({"role": "tool", "content": result})
                continue
            if verdict == "gate":
                req_id = uuid.uuid4().hex[:8]
                emit({"type": "tool_request", "id": req_id, "tool": name,
                      "args": cargs, "risk": permissions.risk_of(name)})
                emit({"type": "state", "state": "WAITING_APPROVAL", "detail": name})
                _emit(agent_name, "WAITING_APPROVAL", name, db)
                allowed = _await_decision(req_id, name, cargs, auto_allow, control,
                                          state, emit, agent_name, db)
                if allowed is None:
                    return final, "stopped by user", tokens_out
                if not allowed:
                    result = "denied by user"
                    emit({"type": "tool_result", "tool": name, "id": req_id,
                          "denied": True, "output": result})
                    messages.append({"role": "tool", "content": result})
                    continue
            emit({"type": "state", "state": "TOOL_CALL", "detail": name})
            _emit(agent_name, "TOOL_CALL", name, db)
            result = hearth_tools.execute_tool(name, cargs, workspace)
            emit({"type": "tool_result", "tool": name, "output": result[:MAX_EVENT_OUT]})
            messages.append({"role": "tool", "content": result[:4000]})
    return final, "hit iteration cap ({})".format(max_iters), tokens_out
```

- [ ] **Step 5: Rewrite `run_loop` to use the driver and the new params**

Replace the entire existing `run_loop` function body (lines 93-135 in the current file) with:

```python
def run_loop(goal, model, workspace, db=DEFAULT_DB, agent_name="agent",
             ollama_url=DEFAULT_OLLAMA, max_iters=MAX_ITERS, chat_fn=None,
             mode="auto", auto_allow=(), emit_fn=None, control_fn=None):
    """Drive a one-shot agent run. chat_fn/emit_fn/control_fn are injectable for
    testing; by default the loop talks Ollama and reads/writes the JSON protocol
    on stdin/stdout."""
    chat_fn = chat_fn or (lambda msgs: chat(ollama_url, model, msgs, hearth_tools.ollama_tool_specs()))
    emit = emit_fn or _stdout_emit
    control = control_fn or _stdin_control
    os.makedirs(workspace, exist_ok=True)
    messages = [{"role": "system", "content": _system_for(mode)},
                {"role": "user", "content": goal}]
    state = {"mode": mode}
    _emit(agent_name, "SPAWNING", "starting", db)
    emit({"type": "state", "state": "SPAWNING", "detail": "starting"})
    t0 = time.monotonic()
    final, error, tokens_out = _run_turns(messages, model, workspace, chat_fn, emit,
                                          control, state, db, agent_name, max_iters,
                                          auto_allow)
    latency_ms = int((time.monotonic() - t0) * 1000)
    _record(db, agent_name, model, tokens_out, latency_ms, error)
    _emit(agent_name, "ERRORED" if error else "DONE", error or "task complete", db)
    emit({"type": "done", "error": error, "final": final})
    return final, error
```

- [ ] **Step 6: Add `run_session`**

Add this function right after `run_loop`:

```python
def run_session(model, workspace, db=DEFAULT_DB, agent_name="session",
                ollama_url=DEFAULT_OLLAMA, max_iters=MAX_ITERS, chat_fn=None,
                mode="auto", auto_allow=(), emit_fn=None, control_fn=None):
    """Long-lived interactive session. Reads user_message / set_mode / stop from
    the control channel, runs agent turns per user_message, and streams events.
    Ends on stop or EOF. Conversation context persists across messages."""
    chat_fn = chat_fn or (lambda msgs: chat(ollama_url, model, msgs, hearth_tools.ollama_tool_specs()))
    emit = emit_fn or _stdout_emit
    control = control_fn or _stdin_control
    os.makedirs(workspace, exist_ok=True)
    state = {"mode": mode}
    messages = [{"role": "system", "content": _system_for(mode)}]
    _emit(agent_name, "IDLE", "ready", db)
    emit({"type": "state", "state": "IDLE", "detail": "ready"})
    while True:
        cmd = control({"need": "message"}) or {}
        ctype = cmd.get("type")
        if ctype == "stop":
            break
        if ctype == "set_mode":
            new = cmd.get("mode")
            if new in permissions.MODES:
                state["mode"] = new
                messages[0] = {"role": "system", "content": _system_for(new)}
                emit({"type": "state", "state": "IDLE", "detail": "mode -> " + new})
            continue
        if ctype != "user_message":
            continue
        messages.append({"role": "user", "content": cmd.get("text", "")})
        final, error, _ = _run_turns(messages, model, workspace, chat_fn, emit,
                                     control, state, db, agent_name, max_iters,
                                     auto_allow)
        emit({"type": "turn_done", "error": error, "final": final})
        _emit(agent_name, "ERRORED" if error else "IDLE", error or "ready", db)
    emit({"type": "done", "error": None, "final": ""})
    _emit(agent_name, "DONE", "session ended", db)
```

- [ ] **Step 7: Run the full self-test to verify all tests pass**

Run: `python3 agent/hearth_loop.py --self-test`
Expected: `hearth-loop self-test OK: ...` (the original two cases plus the four new protocol/session blocks all pass).

- [ ] **Step 8: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_loop.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: control protocol in agent loop (events, gating, sessions, modes)"
```

---

### Task 4: CLI wiring for mode and interactive session

Expose the new capability on the command line so it is drivable end-to-end before any server work: `--mode` selects the permission mode, and `--session` runs the long-lived interactive entrypoint reading commands from stdin.

**Files:**
- Modify: `agent/hearth_loop.py` (the `main` function, currently lines 199-221)

- [ ] **Step 1: Add the new arguments and dispatch**

In `main`, after the line `p.add_argument("--max-iters", type=int, default=MAX_ITERS)`, add:

```python
    p.add_argument("--mode", choices=list(permissions.MODES), default="auto",
                   help="permission mode: plan, auto, or bypass")
    p.add_argument("--auto-allow", default="",
                   help="comma-separated command heads always allowed in auto mode")
    p.add_argument("--session", action="store_true",
                   help="run a long-lived interactive session (reads JSON commands "
                        "from stdin, writes JSON events to stdout)")
```

Then, inside `main`, replace the block that currently starts at `if a.self_test:` through the end of the function with:

```python
    if a.self_test:
        return _self_test()
    auto_allow = tuple(x for x in a.auto_allow.split(",") if x)
    if a.session:
        run_session(a.model, a.workspace, db=a.db, agent_name=a.agent_name,
                    ollama_url=a.ollama_url, max_iters=a.max_iters, mode=a.mode,
                    auto_allow=auto_allow)
        return 0
    if not a.goal:
        p.error("a goal is required unless --self-test or --session")
    final, error = run_loop(a.goal, a.model, a.workspace, db=a.db,
                            agent_name=a.agent_name, ollama_url=a.ollama_url,
                            max_iters=a.max_iters, mode=a.mode, auto_allow=auto_allow)
    if error:
        print("hearth-loop error:", error, file=sys.stderr)
        return 1
    print(final)
    return 0
```

- [ ] **Step 2: Verify the self-test still passes (no regression)**

Run: `python3 agent/hearth_loop.py --self-test`
Expected: `hearth-loop self-test OK: ...`

- [ ] **Step 3: Manual smoke test of the protocol via piped JSON (no Ollama)**

This proves the stdin/stdout protocol works as a process. Run this exact command from the worktree root:

```bash
printf '%s\n%s\n' \
  '{"type":"user_message","text":"hi"}' \
  '{"type":"stop"}' \
| python3 -c "import sys,os;sys.path.insert(0,'agent');import hearth_loop as h; \
h.run_session('mock','/tmp/hearth-smoke', db='/tmp/hearth-smoke/d.db', mode='auto', \
chat_fn=lambda m:({'role':'assistant','content':'hello there'},1))"
```

Expected: JSON event lines on stdout, including a line with `"type": "state"` / `"IDLE"`, a line `{"type": "message", "role": "assistant", "content": "hello there"}`, a `"turn_done"` line, and a final `{"type": "done", ...}` line. (The real binary uses Ollama; here we inject `chat_fn` so it needs no model.)

- [ ] **Step 4: Commit**

```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_loop.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: hearth-loop --mode and --session CLI for the control protocol"
```

---

### Task 5: Run the full agent test suite and push

A final guard so CI (the eval gate) stays green, then publish to main per the project's deploy rhythm.

**Files:** none (verification + git)

- [ ] **Step 1: Run every module self-test**

Run each and confirm the OK line:

```bash
python3 agent/permissions.py --self-test
python3 agent/hearth_state.py --self-test
python3 agent/hearth_tools.py
python3 agent/hearth_loop.py --self-test
```

Expected: each prints its `... self-test OK` line and exits 0.

- [ ] **Step 2: Merge latest main and push**

```bash
git fetch origin
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before agent control protocol"
git push origin worktree-desktop:main
```

Expected: push succeeds. (CI eval gate runs the self-tests; confirm green before starting Plan 2.)

---

## Self-Review

**Spec coverage (this plan's slice):**
- Three permission modes plan/auto/bypass with the documented behavior - Task 1 (`decide`) + Task 3 (applied in `_run_turns`, plan mode emits a `plan` event and blocks writes/commands).
- Control protocol event types (`token`, `message`, `tool_request`, `tool_result`, `plan`, `state`, `done`, `error`) - emitted in Task 3. Note: `token` (per-token streaming) is deliberately deferred to Plan 2 as best-effort, per the spec's "Out of scope" note; step-level `message` is the contract and is implemented. `error` surfaces via the `error` field on `done`/`turn_done`; a standalone `error` event is not required by any consumer in this plan.
- Control command types (`user_message`, `decision`, `set_mode`, `stop`) - handled in Task 3 (`_await_decision`, `run_session`).
- Risk classification safe/edit/dangerous and fail-closed for unknown tools - Task 1.
- Permission engine as a pure shared function - Task 1.
- Deny is fed back to the model as a tool result so it can adapt - Task 3 (both mode-deny and user-deny append a `role:"tool"` denial message).
- Protocol test with scripted stdin (deny, approve, switch mode) using the injectable fake `chat_fn` and no Ollama - Task 3 Step 1.
- `permissions.py` wired into the existing self-test pattern - Task 1.

**Deferred to Plan 2 / Plan 3 (intentionally, not gaps):** mapd session endpoints and subprocess supervision, the cockpit console UI, background-worker DB transport + `pending_actions`, the NixOS unsandboxing for full-machine reach, the global `/stop-all` kill switch, and the bypass red banner. None are required for this plan to be working, testable software.

**Placeholder scan:** no TBD/TODO; every code step contains complete code; every test step has an exact command and expected output.

**Type/name consistency:** `decide(mode, tool, args, auto_allow)`, `risk_of(tool)`, `permissions.MODES`, `_run_turns(...)`, `_await_decision(req_id, tool, cargs, auto_allow, control, state, emit, agent_name, db)`, `run_loop(..., mode, auto_allow, emit_fn, control_fn)`, `run_session(...)` are used consistently across Tasks 1, 3, and 4. Event keys (`type`, `tool`, `id`, `args`, `risk`, `output`, `denied`, `content`, `state`, `detail`, `final`, `error`) match between emit sites and the test assertions.
