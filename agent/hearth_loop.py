#!/usr/bin/env python3
"""hearth agent loop: give the model a goal and tools; it thinks, calls tools,
reads results, and repeats until done (or hits the iteration cap). Uses Ollama's
chat tool-calling. Emits runtime state per step (for the live map) and records
the run. A daily token budget (hearth_budget, HEARTH_DAILY_TOKEN_CAP) pauses
runs at the cap, and operator alerts fan out through hearth_notify (budget,
tripwire, error, and opt-in done via HEARTH_NOTIFY_DONE=on). Standard library
only.

Usage:
  hearth-loop --model qwen2.5-coder --agent-name builder --workspace DIR "GOAL"
  hearth-loop --self-test    # runs the loop against a mock model, no Ollama
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import time
import urllib.request
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_budget  # noqa: E402
import hearth_notify  # noqa: E402
import hearth_router  # noqa: E402
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
CREATE TABLE IF NOT EXISTS tripwires (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT NOT NULL, ts TEXT NOT NULL, tool TEXT, path TEXT, token TEXT, detail TEXT
);
"""

# flight recorder: one row per step of a run (model turns, tool calls, tripwires,
# and the final outcome), so a finished run can be replayed from the audit db.
STEPS_SCHEMA = """
CREATE TABLE IF NOT EXISTS run_steps (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT,
  ts TEXT,
  seq INTEGER,
  kind TEXT,
  tool TEXT,
  args TEXT,
  output TEXT,
  duration_ms INTEGER,
  verdict TEXT
);
"""

MAX_STEP_ARGS = 2000  # cap recorded tool args per step
MAX_STEP_OUT = 4000  # cap recorded output per step

# audit-row error written when the daily token budget circuit breaker fires
BUDGET_ERROR = "budget: daily token cap reached"

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


def _balanced_obj(s, start):
    """Return (substring, end_index) for the brace-balanced {...} starting at
    s[start], honoring quotes/escapes, or (None, start+1) if it never closes."""
    depth = 0
    in_str = False
    esc = False
    for j in range(start, len(s)):
        c = s[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start:j + 1], j + 1
    return None, start + 1


def _lenient_json(text):
    """json.loads, but tolerate the trailing commas local models often emit
    (e.g. {"content":"x",}). Returns the object or None."""
    try:
        return json.loads(text)
    except ValueError:
        try:
            return json.loads(re.sub(r",(\s*[}\]])", r"\1", text))
        except ValueError:
            return None


def parse_content_tool_calls(content, allowed=None):
    """Fallback: extract tool calls a model emitted as JSON text instead of using
    Ollama's structured tool_calls field (common with local models). Scans the
    content for JSON objects that name a known tool and returns a list of
    {name, arguments} dicts. Tolerates trailing-comma JSON. When allowed (the
    run's capability manifest) is given, only manifest tools are recognized, so
    a text-emitted call can never reach a tool the run was not granted."""
    if not content:
        return []
    known = {t["name"] for t in hearth_tools.TOOLS}
    if allowed is not None:
        known &= set(allowed)
    decoder = json.JSONDecoder()
    calls = []
    i = 0
    while i < len(content):
        if content[i] != "{":
            i += 1
            continue
        obj = None
        nxt = i + 1
        try:
            obj, nxt = decoder.raw_decode(content, i)
        except ValueError:
            # Strict parse failed (often a trailing comma). Grab the balanced
            # brace span and retry leniently so the tool call is not lost.
            sub, span_end = _balanced_obj(content, i)
            if sub is not None:
                lenient = _lenient_json(sub)
                if lenient is not None:
                    obj, nxt = lenient, span_end
        if isinstance(obj, dict) and obj.get("name") in known:
            args = obj.get("arguments")
            if not isinstance(args, dict):
                args = obj.get("parameters") if isinstance(obj.get("parameters"), dict) else {}
            calls.append({"name": obj["name"], "arguments": args})
        i = nxt
    return calls


def _result_hint(result):
    """Append a short, actionable hint when a tool result shows a common,
    recoverable failure, so a weak local model can self-correct instead of
    looping on the same mistake. Returns '' when there is nothing to add."""
    low = (result or "").lower()
    if "no module named" in low or "modulenotfounderror" in low:
        return ("\n\n[hint] That Python package is not installed. Use ONLY the "
                "Python standard library (for images, write a PPM/PGM file by "
                "hand), or call a tool that is installed.")
    if "command not found" in low or "not found in path" in low:
        return ("\n\n[hint] That command is not on PATH. Use an installed tool "
                "(ffmpeg, imagemagick's `convert`, yt-dlp, sox, git, python3) or a "
                "different approach.")
    return ""


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


def _plant_decoys_maybe(workspace):
    """Plant honeyfile decoys unless disabled via HEARTH_DECOYS=off. Returns the
    set of planted relpaths (empty when disabled or planting failed)."""
    if os.environ.get("HEARTH_DECOYS", "on").lower() == "off":
        return set()
    try:
        return hearth_tools.plant_decoys(workspace)
    except Exception:  # noqa: BLE001 - a planting failure must not break the run
        return set()


def _env_manifest():
    """Parse the run's capability manifest from HEARTH_ALLOWED_TOOLS
    (comma-separated tool names). Unset or empty means no manifest (None):
    every registered tool is available, subject to the permission mode."""
    raw = os.environ.get("HEARTH_ALLOWED_TOOLS", "")
    names = frozenset(x.strip() for x in raw.split(",") if x.strip())
    return names or None


def _system_for(mode):
    base = SYSTEM_PROMPT
    if mode == "plan":
        base += (" You are in PLAN MODE: do not modify anything and do not run "
                 "commands. Investigate using read-only tools only, then reply "
                 "with a concise step-by-step plan and stop.")
    return base


def _await_decision(req_id, tool, cargs, auto_allow, control, state, emit, agent_name, db,
                    allowed_tools=None):
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
                if permissions.decide(new, tool, cargs, auto_allow,
                                      allowed_tools=allowed_tools) == "allow":
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


def _record_tripwire(db, agent_id, tool, path, token, detail):
    """Write a tripwire row to the audit db. Best-effort."""
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(TRANSCRIPT_SCHEMA + STEPS_SCHEMA)
            con.execute(
                "INSERT INTO tripwires (agent_id, ts, tool, path, token, detail) "
                "VALUES (?,?,?,?,?,?)",
                (agent_id, _now_iso(), tool, path, token, detail))
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        pass


def _record_step(db, agent_id, seq, kind, tool, args, output, duration_ms, verdict):
    """Write one flight-recorder step to the audit db. Best-effort: a recorder
    failure must never break the run."""
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(STEPS_SCHEMA)
            con.execute(
                "INSERT INTO run_steps (agent_id, ts, seq, kind, tool, args, "
                "output, duration_ms, verdict) VALUES (?,?,?,?,?,?,?,?,?)",
                (agent_id, _now_iso(), seq, kind, tool,
                 (args or "")[:MAX_STEP_ARGS], (output or "")[:MAX_STEP_OUT],
                 duration_ms, verdict))
            con.commit()
        finally:
            con.close()
    except sqlite3.Error:
        pass


def _budget_breach(db):
    """Return the daily budget status when today's token spend has reached the
    cap, else None. Cheap when no cap is configured: no db read at all."""
    try:
        if hearth_budget.cap() <= 0:
            return None
        st = hearth_budget.check(db)
        return st if st.get("capped") else None
    except Exception:  # noqa: BLE001 - budget bookkeeping must never break the run
        return None


def _budget_prestep(db, agent_name):
    """A one-row step recorder for a run halted before its turn loop starts
    (the turn loop owns its own step sequence once it is running)."""
    def step(kind, tool, args, output, duration_ms, verdict):
        if os.environ.get("HEARTH_RECORDER", "on").lower() != "off":
            _record_step(db, agent_name, 1, kind, tool, args, output,
                         duration_ms, verdict)
    return step


def _budget_halt(st, emit, step_fn, notified, agent_name):
    """Announce a daily-token-cap breach: a budget event, a flight-recorder
    error row, and at most one operator alert per run (notified is the run's
    shared guard dict). Best-effort throughout."""
    detail = "daily token cap reached ({}/{})".format(st["tokens"], st["cap"])
    emit({"type": "budget", "tokens": st["tokens"], "cap": st["cap"],
          "detail": detail})
    step_fn("error", "", "", detail, 0, "")
    if not notified.get("budget"):
        notified["budget"] = True
        try:
            hearth_notify.notify(
                "budget", "agent {} paused: daily token cap reached ({}/{})".format(
                    agent_name, st["tokens"], st["cap"]))
        except Exception:  # noqa: BLE001 - alerting must never break the run
            pass


def _notify_run_end(agent_name, error):
    """Alert the operator at run end. An errored run always notifies, except a
    budget halt (the breaker already sent its own alert); a clean finish
    notifies only when HEARTH_NOTIFY_DONE=on. Best-effort."""
    try:
        if error:
            if not error.startswith("budget:"):
                hearth_notify.notify("error", "agent {} failed: {}".format(agent_name, error))
        elif os.environ.get("HEARTH_NOTIFY_DONE") == "on":
            hearth_notify.notify("done", "agent {} finished".format(agent_name))
    except Exception:  # noqa: BLE001 - alerting must never break the run
        pass


def _notify_tripwire(agent_name, detail):
    """Alert the operator that a tripwire fired, via every configured channel.
    Dormant (no-op) until a channel is configured."""
    try:
        hearth_notify.notify("tripwire", "{} ({})".format(agent_name, detail))
    except Exception:  # noqa: BLE001 - notification must never break the run
        pass


def _tripwire_hit(tool, cargs, result, decoys, workspace):
    """Return (path, token, reason) if this tool call tripped a honeyfile, else
    None. Two layers: a direct read of a planted decoy path (by name), and a
    canary token surfacing in the tool output (catches shell `cat`, grep, etc.)."""
    # Layer 1: a read/edit/search tool aimed straight at a planted decoy path.
    if tool in ("read_file", "edit_file", "search_files") and decoys:
        raw = cargs.get("path") if isinstance(cargs, dict) else None
        if raw:
            rel = str(raw).lstrip("/")
            norm = os.path.normpath(rel).replace(os.sep, "/")
            for d in decoys:
                if norm == d or norm == os.path.normpath(d).replace(os.sep, "/"):
                    return d, None, "read the decoy file {}".format(d)
    # Layer 2: a canary token appeared in the output (any tool, incl. shell).
    token = hearth_tools.find_canary(result)
    if token:
        return None, token, "a decoy canary token surfaced in {} output".format(tool)
    return None


def _run_turns(messages, model, workspace, chat_fn, emit, control, state,
               db, agent_name, max_iters, auto_allow, allowed_tools=None, decoys=None,
               notified=None):
    """Run agent turns until the model stops calling tools, hits the cap, or is
    stopped. state is a mutable dict holding {"mode": ...}. allowed_tools is the
    run's capability manifest (None = no manifest). notified is a mutable dict
    shared with the caller so the budget alert fires at most once per run.
    Returns (final_text, error, tokens_out)."""
    if notified is None:
        notified = {}
    tokens_out = 0
    final = ""
    # flight recorder: one run_steps row per model turn / tool call / tripwire,
    # plus a final done or error row. HEARTH_RECORDER=off disables it.
    rec_on = os.environ.get("HEARTH_RECORDER", "on").lower() != "off"
    rec_seq = [1]  # mutable so the closure can advance it across the run

    def _step(kind, tool, args, output, duration_ms, verdict):
        if rec_on:
            _record_step(db, agent_name, rec_seq[0], kind, tool, args, output,
                         duration_ms, verdict)
            rec_seq[0] += 1

    for _ in range(max_iters):
        # circuit breaker: refuse the next model call once today's spend is capped
        st = _budget_breach(db)
        if st is not None:
            _budget_halt(st, emit, _step, notified, agent_name)
            return final, BUDGET_ERROR, tokens_out
        emit({"type": "state", "state": "THINKING", "detail": "calling " + model})
        _emit(agent_name, "THINKING", "calling " + model, db)
        t_chat = time.monotonic()
        msg, tout = chat_fn(messages)
        chat_ms = int((time.monotonic() - t_chat) * 1000)
        tokens_out += tout
        messages.append(msg)
        content = msg.get("content", "")
        _step("think", "", "", (content or "")[:500], chat_ms, "")
        if content:
            emit({"type": "message", "role": "assistant", "content": content})
        calls = msg.get("tool_calls") or []
        if not calls:
            parsed = parse_content_tool_calls(content, allowed=allowed_tools)
            if parsed:
                calls = [{"function": c} for c in parsed]
            else:
                final = content
                if state["mode"] == "plan":
                    emit({"type": "plan", "content": content})
                _step("done", "", "", (final or "")[:500], 0, "")
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
            verdict = permissions.decide(state["mode"], name, cargs, auto_allow,
                                         allowed_tools=allowed_tools)
            if verdict == "deny":
                if allowed_tools is not None and name not in allowed_tools:
                    result = ("denied: {} is not in this run's capability manifest; "
                              "use only the tools you were given".format(name))
                else:
                    result = "denied: permission mode '{}' does not allow {}".format(
                        state["mode"], name)
                emit({"type": "tool_result", "tool": name, "denied": True, "output": result})
                messages.append({"role": "tool", "content": result})
                _step("tool", name, json.dumps(cargs), result, 0, "deny")
                continue
            step_verdict = "allow"
            if verdict == "gate":
                req_id = uuid.uuid4().hex[:8]
                emit({"type": "tool_request", "id": req_id, "tool": name,
                      "args": cargs, "risk": permissions.risk_of(name)})
                emit({"type": "state", "state": "WAITING_APPROVAL", "detail": name})
                _emit(agent_name, "WAITING_APPROVAL", name, db)
                allowed = _await_decision(req_id, name, cargs, auto_allow, control,
                                          state, emit, agent_name, db,
                                          allowed_tools=allowed_tools)
                if allowed is None:
                    _step("error", "", "", "stopped by user", 0, "")
                    return final, "stopped by user", tokens_out
                if not allowed:
                    result = "denied by user"
                    emit({"type": "tool_result", "tool": name, "id": req_id,
                          "denied": True, "output": result})
                    messages.append({"role": "tool", "content": result})
                    _step("tool", name, json.dumps(cargs), result, 0, "gate:deny")
                    continue
                step_verdict = "gate:allow"
            # Layer 1: a direct read of a planted decoy path trips before we even
            # execute the tool (so the decoy contents never reach the model).
            hit = _tripwire_hit(name, cargs, "", decoys, workspace)
            if hit is None:
                emit({"type": "state", "state": "TOOL_CALL", "detail": name})
                _emit(agent_name, "TOOL_CALL", name, db)
                t_tool = time.monotonic()
                result = hearth_tools.execute_tool(name, cargs, workspace)
                tool_ms = int((time.monotonic() - t_tool) * 1000)
                _step("tool", name, json.dumps(cargs), result, tool_ms, step_verdict)
                # Layer 2: a canary token surfacing in the output (shell cat, grep).
                hit = _tripwire_hit(name, cargs, result, decoys, workspace)
            else:
                result = ""
            if hit is not None:
                path, token, reason = hit
                _record_tripwire(db, agent_name, name, path, token, reason)
                _step("tripwire", name, "", reason, 0, "tripped")
                emit({"type": "tripwire", "tool": name, "path": path, "token": token,
                      "detail": reason})
                _notify_tripwire(agent_name, reason)
                mode_kill = os.environ.get("HEARTH_TRIPWIRE", "kill").lower() != "flag"
                emit({"type": "state", "state": "TRIPPED", "detail": reason})
                _emit(agent_name, "TRIPPED", reason, db)
                if mode_kill:
                    _step("error", "", "", "tripwire: {}".format(reason), 0, "")
                    return final, "tripwire: {}".format(reason), tokens_out
                # flag mode: record, warn the model, keep going
                warn = "TRIPWIRE: {}. That was a planted decoy secret; stop reading credentials.".format(reason)
                emit({"type": "tool_result", "tool": name, "denied": True, "output": warn})
                messages.append({"role": "tool", "content": warn})
                continue
            emit({"type": "tool_result", "tool": name, "output": result[:MAX_EVENT_OUT]})
            content = result[:MAX_EVENT_OUT] + _result_hint(result)
            messages.append({"role": "tool", "content": content})
    _step("error", "", "", "hit iteration cap ({})".format(max_iters), 0, "")
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
        con.executescript(TRANSCRIPT_SCHEMA + STEPS_SCHEMA)
        return con

    def emit(event):
        try:
            con = _con()
            try:
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
            finally:
                con.close()
        except sqlite3.Error:
            pass

    def control(request):
        req_id = request.get("id")
        while True:
            decision = None
            try:
                con = _con()
                try:
                    row = con.execute(
                        "SELECT decision FROM pending_actions "
                        "WHERE agent_id=? AND req_id=? ORDER BY id DESC LIMIT 1",
                        (agent_id, req_id)).fetchone()
                    decision = row[0] if row else None
                finally:
                    con.close()
            except sqlite3.Error:
                decision = None
            if decision:
                return {"type": "decision", "id": req_id, "allow": decision == "allow"}
            time.sleep(poll_interval)

    return emit, control


def _recalled_context(db, goal, kb_limit=3, mem_limit=3):
    """Pull the knowledge-base chunks and memory lessons most relevant to the
    goal, so an agent starts grounded without having to search first. Best-effort:
    any failure (or an empty store) returns ''. Lazy imports keep this optional."""
    parts = []
    try:
        import hearth_knowledge
        hits = hearth_knowledge.search(db, goal, limit=kb_limit)
        ctx = hearth_knowledge.as_context(hits)
        if ctx:
            parts.append(ctx)
    except Exception:  # noqa: BLE001
        pass
    try:
        import hearth_memory
        lessons = hearth_memory.recall(db, goal, limit=mem_limit)
        ctx = hearth_memory.as_context(lessons)
        if ctx:
            parts.append(ctx)
    except Exception:  # noqa: BLE001
        pass
    return "\n\n".join(parts)


def _resolve_auto_model(model, goal, allowed_tools, emit, db, agent_name):
    """When a run is launched with model 'auto' (or no model), consult the hearth
    router to pick a concrete model for this goal and toolset, so a replay shows
    which model ran and why. Emits one route event and records a think step in the
    flight recorder. Best-effort: any router failure keeps the fallback default.
    Returns the resolved model name (the caller's model unchanged when not 'auto')."""
    if model and str(model).lower() != "auto":
        return model
    fallback = os.environ.get("HEARTH_DEFAULT_MODEL") or "llama3.2:3b"
    chosen = fallback
    why = "router unavailable; used default"
    try:
        rules = hearth_router.load_rules()
        picked = hearth_router.choose_model(goal, tools=allowed_tools, rules=rules,
                                            fallback=fallback)
        if picked:
            chosen = picked
        why = hearth_router.explain(goal, allowed_tools, rules).get("why") or why
    except Exception:  # noqa: BLE001 - a router failure must never break the run
        chosen = fallback
        why = "router unavailable; used default"
    try:
        emit({"type": "route", "model": chosen, "why": why})
    except Exception:  # noqa: BLE001
        pass
    if os.environ.get("HEARTH_RECORDER", "on").lower() != "off":
        _record_step(db, agent_name, 0, "think", "", "",
                     "routed model 'auto' -> {} ({})".format(chosen, why), 0, "route")
    return chosen


def run_loop(goal, model, workspace, db=DEFAULT_DB, agent_name="agent",
             ollama_url=DEFAULT_OLLAMA, max_iters=MAX_ITERS, chat_fn=None,
             mode="auto", auto_allow=(), emit_fn=None, control_fn=None, recall=True,
             allowed_tools=None):
    """Drive a one-shot agent run. chat_fn/emit_fn/control_fn are injectable for
    testing; by default the loop talks Ollama and reads/writes the JSON protocol
    on stdin/stdout. allowed_tools is the run's capability manifest; when None it
    falls back to HEARTH_ALLOWED_TOOLS from the environment (the spawn path)."""
    if allowed_tools is None:
        allowed_tools = _env_manifest()
    emit = emit_fn or _stdout_emit
    control = control_fn or _stdin_control
    # resolve model "auto" via the router before the first model call, so the
    # chat_fn closure and the audit row both use the model actually chosen.
    model = _resolve_auto_model(model, goal, allowed_tools, emit, db, agent_name)
    chat_fn = chat_fn or (lambda msgs: chat(ollama_url, model, msgs,
                                            hearth_tools.ollama_tool_specs(allowed_tools)))
    os.environ["HEARTH_AGENT_ID"] = agent_name  # attribution for the egress log
    notified = {}
    st = _budget_breach(db)
    if st is not None:
        # circuit breaker: today's spend is already at the cap; refuse to start
        _budget_halt(st, emit, _budget_prestep(db, agent_name), notified, agent_name)
        _record(db, agent_name, model, 0, 0, BUDGET_ERROR)
        _emit(agent_name, "ERRORED", BUDGET_ERROR, db)
        emit({"type": "done", "error": BUDGET_ERROR, "final": ""})
        return "", BUDGET_ERROR
    os.makedirs(workspace, exist_ok=True)
    decoys = _plant_decoys_maybe(workspace)
    messages = [{"role": "system", "content": _system_for(mode)}]
    if recall:
        ctx = _recalled_context(db, goal)
        if ctx:
            messages.append({"role": "system", "content":
                             "Context retrieved from hearth's knowledge base and past runs. "
                             "Use it if relevant:\n" + ctx})
    messages.append({"role": "user", "content": goal})
    state = {"mode": mode}
    _emit(agent_name, "SPAWNING", "starting", db)
    emit({"type": "state", "state": "SPAWNING", "detail": "starting"})
    t0 = time.monotonic()
    final, error, tokens_out = _run_turns(messages, model, workspace, chat_fn, emit,
                                          control, state, db, agent_name, max_iters,
                                          auto_allow, allowed_tools=allowed_tools,
                                          decoys=decoys, notified=notified)
    latency_ms = int((time.monotonic() - t0) * 1000)
    _record(db, agent_name, model, tokens_out, latency_ms, error)
    _notify_run_end(agent_name, error)
    final_state = "TRIPPED" if (error or "").startswith("tripwire:") else ("ERRORED" if error else "DONE")
    _emit(agent_name, final_state, error or "task complete", db)
    emit({"type": "done", "error": error, "final": final})
    return final, error


def run_session(model, workspace, db=DEFAULT_DB, agent_name="session",
                ollama_url=DEFAULT_OLLAMA, max_iters=MAX_ITERS, chat_fn=None,
                mode="auto", auto_allow=(), emit_fn=None, control_fn=None,
                allowed_tools=None):
    """Long-lived interactive session. Reads user_message / set_mode / stop from
    the control channel, runs agent turns per user_message, and streams events.
    Ends on stop or EOF. Conversation context persists across messages."""
    if allowed_tools is None:
        allowed_tools = _env_manifest()
    emit = emit_fn or _stdout_emit
    control = control_fn or _stdin_control
    # resolve model "auto" via the router before the first model call, so the
    # chat_fn closure and the audit row both use the model actually chosen. A
    # session has no upfront goal text, so route on the granted tools alone.
    model = _resolve_auto_model(model, "", allowed_tools, emit, db, agent_name)
    chat_fn = chat_fn or (lambda msgs: chat(ollama_url, model, msgs,
                                            hearth_tools.ollama_tool_specs(allowed_tools)))
    os.environ["HEARTH_AGENT_ID"] = agent_name  # attribution for the egress log
    notified = {}
    st = _budget_breach(db)
    if st is not None:
        # circuit breaker: today's spend is already at the cap; refuse to start
        _budget_halt(st, emit, _budget_prestep(db, agent_name), notified, agent_name)
        _record(db, agent_name, model, 0, 0, BUDGET_ERROR)
        _emit(agent_name, "ERRORED", BUDGET_ERROR, db)
        emit({"type": "done", "error": BUDGET_ERROR, "final": ""})
        return
    os.makedirs(workspace, exist_ok=True)
    decoys = _plant_decoys_maybe(workspace)
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
                                               auto_allow, allowed_tools=allowed_tools,
                                               decoys=decoys, notified=notified)
        latency_ms = int((time.monotonic() - t0) * 1000)
        _record(db, agent_name, model, tokens_out, latency_ms, error)
        if error:
            _notify_run_end(agent_name, error)
        emit({"type": "turn_done", "error": error, "final": final})
        if error == BUDGET_ERROR:
            # the daily token cap ends the whole session, not just the turn
            _emit(agent_name, "ERRORED", error, db)
            emit({"type": "done", "error": error, "final": ""})
            return
        if (error or "").startswith("tripwire:"):
            # a tripwire ends the whole session, not just the turn; leave the
            # agent in TRIPPED (already emitted by _run_turns) and stop here
            emit({"type": "done", "error": error, "final": ""})
            return
        _emit(agent_name, "ERRORED" if error else "IDLE", error or "ready", db)
    emit({"type": "done", "error": None, "final": ""})
    _emit(agent_name, "DONE", "session ended", db)
    _notify_run_end(agent_name, None)


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
    # tolerant tool-call parsing: a trailing comma (as llama3.2 emits) must still
    # yield the tool call, not be silently dropped.
    tc = parse_content_tool_calls('{"name":"write_file","parameters":{"path":"a.md","content":"x",}}')
    assert tc == [{"name": "write_file", "arguments": {"path": "a.md", "content": "x"}}], tc
    # clean JSON and the structured "arguments" key still work
    tc2 = parse_content_tool_calls('chatter {"name":"read_file","arguments":{"path":"a"}} more')
    assert tc2 == [{"name": "read_file", "arguments": {"path": "a"}}], tc2
    # unbalanced / non-tool braces are ignored without crashing
    assert parse_content_tool_calls('{"name":"nope"} {oops') == [], "unknown tool + junk"
    # result hints fire on recoverable failures, stay silent otherwise
    assert "standard library" in _result_hint("Traceback ... No module named 'PIL'")
    assert "PATH" in _result_hint("convert: command not found")
    assert _result_hint("wrote 42 bytes") == ""

    ws = tempfile.mkdtemp(prefix="hearth-loop-")
    db = os.path.join(ws, "audit.db")
    # auto-recall: with a knowledge base populated, the recalled context surfaces
    # the relevant chunk for a goal (and run_loop injects it as a system message).
    import hearth_knowledge
    hearth_knowledge.ingest(db, "nix.md", "NixOS rollback is atomic and reproducible from a flake.")
    rctx = _recalled_context(db, "rollback flake atomic")
    assert "rollback" in rctx.lower(), ("recall surfaces the relevant chunk", rctx)
    assert _recalled_context(db, "zzzqqq nothing matches") == "", "no lexical overlap -> no context"
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

    # --- Capability manifest: a tool outside the manifest is denied even in
    # bypass mode, and a text-emitted call to it is not recognized at all.
    events_m = []
    chat_steps_m = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "run_command",
            "arguments": {"command": "echo nope"}}}]}, 1),
        ({"role": "assistant", "content": "ok"}, 1),
    ]
    mseq = iter(chat_steps_m)
    wsm = tempfile.mkdtemp(prefix="hearth-loopM-")
    fm, em = run_loop("try shell", "mock", wsm, db=os.path.join(wsm, "d.db"),
                      agent_name="tm", mode="bypass",
                      chat_fn=lambda m: next(mseq),
                      emit_fn=events_m.append,
                      control_fn=lambda req: {"type": "stop"},
                      allowed_tools=frozenset({"read_file", "write_file"}))
    mdenials = [e for e in events_m if e["type"] == "tool_result" and e.get("denied")]
    assert mdenials and "capability manifest" in mdenials[0]["output"], events_m
    assert em is None, em
    # a manifest tool still works inside the manifest
    events_m2 = []
    chat_steps_m2 = [
        ({"role": "assistant", "tool_calls": [{"function": {"name": "write_file",
            "arguments": {"path": "ok.txt", "content": "in-manifest"}}}]}, 1),
        ({"role": "assistant", "content": "done"}, 1),
    ]
    m2seq = iter(chat_steps_m2)
    fm2, em2 = run_loop("write", "mock", wsm, db=os.path.join(wsm, "d.db"),
                        agent_name="tm2", mode="bypass",
                        chat_fn=lambda m: next(m2seq),
                        emit_fn=events_m2.append,
                        control_fn=lambda req: {"type": "stop"},
                        allowed_tools=frozenset({"read_file", "write_file"}))
    assert em2 is None, em2
    with open(os.path.join(wsm, "ok.txt")) as fh:
        assert fh.read() == "in-manifest"
    # text-emitted calls: outside the manifest -> not recognized; inside -> parsed
    assert parse_content_tool_calls('{"name":"run_command","arguments":{"command":"x"}}',
                                    allowed={"read_file"}) == []
    assert parse_content_tool_calls('{"name":"read_file","arguments":{"path":"a"}}',
                                    allowed={"read_file"}) == [
        {"name": "read_file", "arguments": {"path": "a"}}]
    # env fallback: HEARTH_ALLOWED_TOOLS shapes the manifest when no param given
    os.environ["HEARTH_ALLOWED_TOOLS"] = "read_file, kb_search"
    try:
        assert _env_manifest() == frozenset({"read_file", "kb_search"})
    finally:
        os.environ.pop("HEARTH_ALLOWED_TOOLS", None)
    assert _env_manifest() is None

    # --- Tripwire: an agent that reads a planted decoy trips the alarm, the run
    # dies with a tripwire error, a tripwires row is written, and the state is
    # TRIPPED. Two cases: direct read-by-path (layer 1) and a shell cat surfacing
    # the canary in output (layer 2).
    for label, tool_call in (
        ("read", {"name": "read_file", "arguments": {"path": ".aws/credentials"}}),
        ("cat", {"name": "run_command", "arguments": {"command": "cat .env.production"}}),
    ):
        events_t = []
        tsteps = iter([
            ({"role": "assistant", "tool_calls": [{"function": tool_call}]}, 1),
            ({"role": "assistant", "content": "should not get here"}, 1),
        ])
        wst = tempfile.mkdtemp(prefix="hearth-trip-" + label + "-")
        dbt = os.path.join(wst, "d.db")
        ft, et = run_loop("poke around", "mock", wst, db=dbt, agent_name="trip-" + label,
                          mode="bypass", chat_fn=lambda m: next(tsteps),
                          emit_fn=events_t.append, control_fn=lambda req: {"type": "stop"})
        assert (et or "").startswith("tripwire:"), (label, et)
        assert any(e["type"] == "tripwire" for e in events_t), (label, [e["type"] for e in events_t])
        assert any(e["type"] == "state" and e["state"] == "TRIPPED" for e in events_t), label
        con = sqlite3.connect(dbt)
        rows = con.execute("SELECT tool, detail FROM tripwires WHERE agent_id=?",
                           ("trip-" + label,)).fetchall()
        st = con.execute("SELECT state FROM agent_state WHERE agent_id=?", ("trip-" + label,)).fetchone()
        con.close()
        assert rows, (label, "expected a tripwires row")
        assert st and st[0] == "TRIPPED", (label, st)
    # flag mode (HEARTH_TRIPWIRE=flag): the run is warned but not killed
    os.environ["HEARTH_TRIPWIRE"] = "flag"
    try:
        events_f = []
        fsteps = iter([
            ({"role": "assistant", "tool_calls": [{"function":
                {"name": "read_file", "arguments": {"path": ".env.production"}}}]}, 1),
            ({"role": "assistant", "content": "done exploring"}, 1),
        ])
        wsf = tempfile.mkdtemp(prefix="hearth-tripflag-")
        ff, ef = run_loop("poke", "mock", wsf, db=os.path.join(wsf, "d.db"),
                          agent_name="trip-flag", mode="bypass",
                          chat_fn=lambda m: next(fsteps),
                          emit_fn=events_f.append, control_fn=lambda req: {"type": "stop"})
        assert ef is None, ("flag mode does not kill the run", ef)
        assert any(e["type"] == "tripwire" for e in events_f), "flag mode still records the trip"
    finally:
        os.environ.pop("HEARTH_TRIPWIRE", None)
    # decoys disabled: HEARTH_DECOYS=off plants nothing, so no trip
    os.environ["HEARTH_DECOYS"] = "off"
    try:
        wsn = tempfile.mkdtemp(prefix="hearth-nodecoy-")
        assert _plant_decoys_maybe(wsn) == set()
        assert not os.path.exists(os.path.join(wsn, ".aws", "credentials"))
    finally:
        os.environ.pop("HEARTH_DECOYS", None)

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

    # --- Flight recorder: a run writes think, tool, and done steps to run_steps
    # in ascending seq order; HEARTH_RECORDER=off writes nothing.
    wsr = tempfile.mkdtemp(prefix="hearth-loopR-")
    dbr = os.path.join(wsr, "d.db")
    rsteps = iter([
        ({"role": "assistant", "tool_calls": [{"function": {"name": "write_file",
            "arguments": {"path": "r.txt", "content": "rec"}}}]}, 1),
        ({"role": "assistant", "content": "wrote r.txt"}, 1),
    ])
    fr, er = run_loop("write r.txt", "mock", wsr, db=dbr, agent_name="rec",
                      chat_fn=lambda m: next(rsteps))
    assert er is None, er
    con = sqlite3.connect(dbr)
    rrows = con.execute(
        "SELECT seq, kind, tool, verdict, duration_ms, output FROM run_steps "
        "WHERE agent_id='rec' ORDER BY seq").fetchall()
    con.close()
    rseqs = [r[0] for r in rrows]
    assert rseqs == sorted(rseqs) and len(set(rseqs)) == len(rseqs), rrows
    rkinds = [r[1] for r in rrows]
    assert "think" in rkinds, rrows
    rtools = [r for r in rrows if r[1] == "tool"]
    assert rtools and rtools[0][2] == "write_file", rrows
    assert rtools[0][3] == "allow" and rtools[0][4] >= 0, rrows
    assert rkinds[-1] == "done" and "wrote r.txt" in rrows[-1][5], rrows
    os.environ["HEARTH_RECORDER"] = "off"
    try:
        wso = tempfile.mkdtemp(prefix="hearth-loopRoff-")
        dbo = os.path.join(wso, "d.db")
        osteps = iter([({"role": "assistant", "content": "nothing to do"}, 1)])
        fo, eo = run_loop("noop", "mock", wso, db=dbo, agent_name="rec-off",
                          chat_fn=lambda m: next(osteps))
        assert eo is None, eo
        con = sqlite3.connect(dbo)
        con.executescript(STEPS_SCHEMA)  # the table may not even exist yet
        nrows = con.execute("SELECT COUNT(*) FROM run_steps").fetchone()[0]
        con.close()
        assert nrows == 0, nrows
    finally:
        os.environ.pop("HEARTH_RECORDER", None)

    # --- Budget breaker: with today's spend pre-seeded over the cap, a run
    # halts before the first model call, records the budget error on the audit
    # row, writes a run_steps error row, and sends exactly one budget alert;
    # with the cap unset the same script completes normally.
    def _seed_spend(db_path, name, tin, tout):
        c = sqlite3.connect(db_path)
        c.execute(
            "CREATE TABLE IF NOT EXISTS agent_runs (id INTEGER PRIMARY KEY, "
            "agent_name TEXT, run_id TEXT, started_at TEXT, finished_at TEXT, "
            "tokens_in INTEGER, tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER, "
            "error TEXT, model TEXT)")
        ts = _now_iso()
        c.execute(
            "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
            "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            (name, uuid.uuid4().hex, ts, ts, tin, tout, 0.0, 1, None, "mock"))
        c.commit()
        c.close()

    wsb = tempfile.mkdtemp(prefix="hearth-loopB-")
    dbb = os.path.join(wsb, "d.db")
    _seed_spend(dbb, "seed", 400, 200)
    saved_cap = os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
    saved_done = os.environ.pop("HEARTH_NOTIFY_DONE", None)
    sent = []
    real_notify = hearth_notify.notify
    hearth_notify.notify = lambda kind, text, post_fn=None: sent.append((kind, text)) or 0
    try:
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "100"
        events_b = []
        model_calls = []

        def bchat(m):
            model_calls.append(1)
            return {"role": "assistant", "content": "should never run"}, 1

        fb, eb = run_loop("noop", "mock", wsb, db=dbb, agent_name="bud",
                          chat_fn=bchat, emit_fn=events_b.append,
                          control_fn=lambda req: {"type": "stop"})
        assert eb == "budget: daily token cap reached", eb
        assert model_calls == [], "a capped run must not call the model"
        assert any(e["type"] == "budget" for e in events_b), events_b
        con = sqlite3.connect(dbb)
        brun = con.execute("SELECT error FROM agent_runs WHERE agent_name='bud'").fetchone()
        bsteps = con.execute("SELECT kind, output FROM run_steps WHERE agent_id='bud'").fetchall()
        con.close()
        assert brun and brun[0] == "budget: daily token cap reached", brun
        assert any(k == "error" and "daily token cap reached (600/100)" in (o or "")
                   for k, o in bsteps), bsteps
        assert sent == [("budget", "agent bud paused: daily token cap reached (600/100)")], sent

        # mid-run breach: spend crosses the cap between turns (another run
        # lands); the loop halts before the next model call, alerting once
        del sent[:]
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "1000"
        wsb2 = tempfile.mkdtemp(prefix="hearth-loopB2-")
        dbb2 = os.path.join(wsb2, "d.db")
        events_b2 = []

        def b2chat(m):
            _seed_spend(dbb2, "other", 5000, 0)  # another run blows the cap
            return ({"role": "assistant", "tool_calls": [{"function": {"name": "write_file",
                     "arguments": {"path": "b2.txt", "content": "x"}}}]}, 1)

        fb2, eb2 = run_loop("noop", "mock", wsb2, db=dbb2, agent_name="bud2",
                            chat_fn=b2chat, emit_fn=events_b2.append,
                            control_fn=lambda req: {"type": "stop"})
        assert eb2 == "budget: daily token cap reached", eb2
        assert len([e for e in events_b2 if e["type"] == "budget"]) == 1, events_b2
        con = sqlite3.connect(dbb2)
        brun2 = con.execute("SELECT error FROM agent_runs WHERE agent_name='bud2'").fetchone()
        con.close()
        assert brun2 and brun2[0] == "budget: daily token cap reached", brun2
        assert [s for s in sent if s[0] == "budget"] == [
            ("budget", "agent bud2 paused: daily token cap reached (5000/1000)")], sent
        assert not [s for s in sent if s[0] == "error"], ("budget halts alert once", sent)

        # cap unset: the same pre-seeded db no longer blocks; the run completes,
        # and HEARTH_NOTIFY_DONE=on sends the finish alert
        os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
        os.environ["HEARTH_NOTIFY_DONE"] = "on"
        del sent[:]
        oksteps = iter([({"role": "assistant", "content": "all done"}, 1)])
        fok, eok = run_loop("noop", "mock", wsb, db=dbb, agent_name="bud-ok",
                            chat_fn=lambda m: next(oksteps),
                            emit_fn=lambda e: None,
                            control_fn=lambda req: {"type": "stop"})
        assert eok is None and fok == "all done", (fok, eok)
        assert ("done", "agent bud-ok finished") in sent, sent
    finally:
        hearth_notify.notify = real_notify
        if saved_cap is None:
            os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
        else:
            os.environ["HEARTH_DAILY_TOKEN_CAP"] = saved_cap
        if saved_done is None:
            os.environ.pop("HEARTH_NOTIFY_DONE", None)
        else:
            os.environ["HEARTH_NOTIFY_DONE"] = saved_done

    # --- Model router: an "auto" launch with a router.json that maps a "python"
    # keyword to a specific model resolves to that model, emits a route event, and
    # records it on the audit row; an explicit model is left untouched (no route).
    rdir = tempfile.mkdtemp(prefix="hearth-loop-router-")
    rcfg = os.path.join(rdir, "router.json")
    with open(rcfg, "w") as fh:
        json.dump({"default": "llama3.2:3b",
                   "rules": [{"name": "code",
                              "any_keywords": ["python", "refactor"],
                              "model": "qwen2.5-coder:latest"}]}, fh)
    saved_router_default = hearth_router.DEFAULT_RULES
    saved_router_env = os.environ.pop("HEARTH_ROUTER", None)
    try:
        os.environ["HEARTH_ROUTER"] = rcfg
        # load_rules() reads its default path, frozen at import; point it here too
        hearth_router.DEFAULT_RULES = rcfg
        wsrt = tempfile.mkdtemp(prefix="hearth-loop-auto-")
        dbrt = os.path.join(wsrt, "d.db")
        ev_rt = []
        rtsteps = iter([({"role": "assistant", "content": "done"}, 1)])
        frt, ert = run_loop("write a python function", "auto", wsrt, db=dbrt,
                            agent_name="route-auto", chat_fn=lambda m: next(rtsteps),
                            emit_fn=ev_rt.append, control_fn=lambda req: {"type": "stop"})
        assert ert is None, ert
        assert any(e["type"] == "route" and e["model"] == "qwen2.5-coder:latest"
                   for e in ev_rt), ev_rt
        con = sqlite3.connect(dbrt)
        rmodel = con.execute(
            "SELECT model FROM agent_runs WHERE agent_name='route-auto'").fetchone()
        con.close()
        assert rmodel and rmodel[0] == "qwen2.5-coder:latest", rmodel
        # an explicit model is recorded unchanged; the router is not consulted
        wsre = tempfile.mkdtemp(prefix="hearth-loop-explicit-")
        dbre = os.path.join(wsre, "d.db")
        ev_re = []
        resteps = iter([({"role": "assistant", "content": "done"}, 1)])
        fre, ere = run_loop("write a python function", "mock", wsre, db=dbre,
                            agent_name="route-explicit", chat_fn=lambda m: next(resteps),
                            emit_fn=ev_re.append, control_fn=lambda req: {"type": "stop"})
        assert ere is None, ere
        assert not any(e["type"] == "route" for e in ev_re), ev_re
        con = sqlite3.connect(dbre)
        emodel = con.execute(
            "SELECT model FROM agent_runs WHERE agent_name='route-explicit'").fetchone()
        con.close()
        assert emodel and emodel[0] == "mock", emodel
    finally:
        hearth_router.DEFAULT_RULES = saved_router_default
        if saved_router_env is None:
            os.environ.pop("HEARTH_ROUTER", None)
        else:
            os.environ["HEARTH_ROUTER"] = saved_router_env

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
    p.add_argument("--allowed-tools", default="",
                   help="comma-separated capability manifest: the run may use ONLY "
                        "these tools, in every mode (falls back to HEARTH_ALLOWED_TOOLS)")
    p.add_argument("--session", action="store_true",
                   help="run a long-lived interactive session (reads JSON commands "
                        "from stdin, writes JSON events to stdout)")
    p.add_argument("--manager", action="store_true",
                   help="run as a swarm manager (decompose a goal and spawn specialists)")
    p.add_argument("--marathon", action="store_true",
                   help="run loop-until-done (work in rounds until the goal is complete)")
    p.add_argument("--evolve", action="store_true",
                   help="self-evolve: propose a change to hearth's own config and validate it builds")
    p.add_argument("--grow", action="store_true",
                   help="growth loop: continuously propose, implement, and validate self-improvements")
    p.add_argument("--max-cycles", type=int, default=25,
                   help="grow: number of self-improvement cycles before stopping")
    p.add_argument("--checkin", action="store_true",
                   help="marathon: DM progress and wait for a Telegram reply each round")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    auto_allow = tuple(x for x in a.auto_allow.split(",") if x)
    allowed_tools = frozenset(x.strip() for x in a.allowed_tools.split(",") if x.strip()) or None
    emit_fn = control_fn = None
    if a.io == "db":
        emit_fn, control_fn = make_db_transport(a.db, a.agent_name)
    if a.manager:
        import hearth_swarm  # lazy import to avoid an import cycle
        if not a.goal:
            p.error("a goal is required with --manager")
        final = hearth_swarm.run_manager(a.goal, a.model, a.workspace, db=a.db,
                                         agent_id=a.agent_name, mode=a.mode,
                                         ollama_url=a.ollama_url)
        print(final)
        return 0
    if a.marathon:
        import hearth_marathon  # lazy import to avoid a cycle
        if not a.goal:
            p.error("a goal is required with --marathon")
        final = hearth_marathon.run_marathon(a.goal, a.model, a.workspace, db=a.db,
                                             agent_id=a.agent_name, mode=a.mode,
                                             ollama_url=a.ollama_url, checkin=a.checkin)
        print(final)
        return 0
    if a.evolve:
        import hearth_evolve  # lazy import to avoid a cycle
        if not a.goal:
            p.error("a goal is required with --evolve")
        msg = hearth_evolve.run_evolve(a.goal, a.model, db=a.db, agent_id=a.agent_name,
                                       ollama_url=a.ollama_url)
        print(msg or "self-evolve did not produce a valid branch")
        return 0
    if a.grow:
        import hearth_grow  # lazy import to avoid a cycle
        summary = hearth_grow.run_growth(a.model, db=a.db, agent_id=a.agent_name,
                                         ollama_url=a.ollama_url, max_cycles=a.max_cycles)
        print(summary)
        return 0
    if a.session and a.goal:
        p.error("--session does not take a positional goal; send goals via stdin")
    if a.session:
        run_session(a.model, a.workspace, db=a.db, agent_name=a.agent_name,
                    ollama_url=a.ollama_url, max_iters=a.max_iters, mode=a.mode,
                    auto_allow=auto_allow, emit_fn=emit_fn, control_fn=control_fn,
                    allowed_tools=allowed_tools)
        return 0
    if not a.goal:
        p.error("a goal is required unless --self-test or --session")
    final, error = run_loop(a.goal, a.model, a.workspace, db=a.db,
                            agent_name=a.agent_name, ollama_url=a.ollama_url,
                            max_iters=a.max_iters, mode=a.mode, auto_allow=auto_allow,
                            emit_fn=emit_fn, control_fn=control_fn,
                            allowed_tools=allowed_tools)
    if error:
        print("hearth-loop error:", error, file=sys.stderr)
        return 1
    print(final)
    return 0


if __name__ == "__main__":
    sys.exit(main())
