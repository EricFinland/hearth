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
import permissions  # noqa: E402
try:
    import hearth_state  # noqa: E402
except Exception:  # noqa: BLE001
    hearth_state = None

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
MAX_ITERS = 12
MAX_EVENT_OUT = 4000  # cap tool output included in an event

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


def parse_content_tool_calls(content):
    """Fallback: extract tool calls a model emitted as JSON text instead of using
    Ollama's structured tool_calls field (common with local models). Scans the
    content for JSON objects that name a known tool and returns a list of
    {name, arguments} dicts."""
    if not content:
        return []
    known = {t["name"] for t in hearth_tools.TOOLS}
    decoder = json.JSONDecoder()
    calls = []
    i = 0
    while i < len(content):
        if content[i] != "{":
            i += 1
            continue
        try:
            obj, end = decoder.raw_decode(content, i)
        except ValueError:
            i += 1
            continue
        if isinstance(obj, dict) and obj.get("name") in known:
            args = obj.get("arguments")
            if not isinstance(args, dict):
                args = obj.get("parameters") if isinstance(obj.get("parameters"), dict) else {}
            calls.append({"name": obj["name"], "arguments": args})
        i = end
    return calls


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
                _emit(agent_name, "THINKING", "mode -> " + new, db)
                if permissions.decide(new, tool, cargs, auto_allow) == "allow":
                    return True
                # still gated under the new mode: stay parked, re-advertise so the UI
                # keeps showing the pending request rather than a stale THINKING.
                emit({"type": "state", "state": "WAITING_APPROVAL", "detail": tool})
                _emit(agent_name, "WAITING_APPROVAL", tool, db)
            continue
        # an omitted id is a wildcard for the current pending request
        if ctype == "decision" and cmd.get("id") in (req_id, None):
            return bool(cmd.get("allow"))
        if ctype == "user_message":
            emit({"type": "notice", "detail": "message ignored: approval pending"})
            continue
        # any other command type is ignored; keep waiting for a decision


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
            if isinstance(raw, dict):
                cargs = raw
            elif raw:
                try:
                    cargs = json.loads(raw)
                except (ValueError, TypeError):
                    emit({"type": "notice", "detail": "could not parse arguments for " + name})
                    cargs = {}
            else:
                cargs = {}
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
            messages.append({"role": "tool", "content": result[:MAX_EVENT_OUT]})
    return final, "hit iteration cap ({})".format(max_iters), tokens_out


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
        t0 = time.monotonic()
        final, error, tokens_out = _run_turns(messages, model, workspace, chat_fn, emit,
                                               control, state, db, agent_name, max_iters,
                                               auto_allow)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _record(db, agent_name, model, tokens_out, latency_ms, error)
        emit({"type": "turn_done", "error": error, "final": final})
        _emit(agent_name, "ERRORED" if error else "IDLE", error or "ready", db)
    emit({"type": "done", "error": None, "final": ""})
    _emit(agent_name, "DONE", "session ended", db)


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

    # Fallback path: a model that emits its tool call as JSON text (no structured
    # tool_calls field) must still get the tool executed.
    ws2 = tempfile.mkdtemp(prefix="hearth-loop2-")
    steps2 = [
        ({"role": "assistant", "content":
            'sure, doing that:\n{"name": "write_file", "arguments": '
            '{"path": "c.txt", "content": "yo"}}'}, 1),
        ({"role": "assistant", "content": "wrote c.txt"}, 1),
    ]
    seq2 = iter(steps2)
    final2, err2 = run_loop("write c.txt", "mock", ws2, db=os.path.join(ws2, "d.db"),
                            agent_name="t2", chat_fn=lambda msgs: next(seq2))
    assert err2 is None, err2
    with open(os.path.join(ws2, "c.txt")) as fh:
        assert fh.read() == "yo"

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
    p.add_argument("--mode", choices=list(permissions.MODES), default="auto",
                   help="permission mode: plan, auto, or bypass")
    p.add_argument("--io", choices=["stdio", "db"], default="stdio",
                   help="event/control transport: stdio (interactive) or db (background worker)")
    p.add_argument("--auto-allow", default="",
                   help="comma-separated command heads always allowed in auto mode")
    p.add_argument("--session", action="store_true",
                   help="run a long-lived interactive session (reads JSON commands "
                        "from stdin, writes JSON events to stdout)")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    auto_allow = tuple(x for x in a.auto_allow.split(",") if x)
    emit_fn = control_fn = None
    if a.io == "db":
        emit_fn, control_fn = make_db_transport(a.db, a.agent_name)
    if a.session and a.goal:
        p.error("--session does not take a positional goal; send goals via stdin")
    if a.session:
        run_session(a.model, a.workspace, db=a.db, agent_name=a.agent_name,
                    ollama_url=a.ollama_url, max_iters=a.max_iters, mode=a.mode,
                    auto_allow=auto_allow, emit_fn=emit_fn, control_fn=control_fn)
        return 0
    if not a.goal:
        p.error("a goal is required unless --self-test or --session")
    final, error = run_loop(a.goal, a.model, a.workspace, db=a.db,
                            agent_name=a.agent_name, ollama_url=a.ollama_url,
                            max_iters=a.max_iters, mode=a.mode, auto_allow=auto_allow,
                            emit_fn=emit_fn, control_fn=control_fn)
    if error:
        print("hearth-loop error:", error, file=sys.stderr)
        return 1
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
