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
                parsed = parse_content_tool_calls(msg.get("content", ""))
                if parsed:
                    calls = [{"function": c} for c in parsed]
                else:
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
