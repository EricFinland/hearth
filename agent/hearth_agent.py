#!/usr/bin/env python3
"""hearth-agent: run one prompt against the local Ollama server and record the
run to the hearth audit database.

This is deliberately dependency free (Python standard library only). That keeps
it trivial to package with Nix and easy to audit by reading a single file. It
talks to Ollama over plain HTTP and writes to SQLite directly.

Usage:
  hearth-agent "Summarize the following in one sentence: ..."
  hearth-agent --model mistral:7b --agent-name summarizer "..."
  hearth-agent --init-db          # create the audit schema, then exit
  hearth-agent --self-test        # exercise the audit path without Ollama
"""

import argparse
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_RUNS_DIR = "/var/lib/hearth/runs"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"

# Live runtime state for the tycoon map (agent/hearth_state.py). Optional: if it
# is not importable, state emission is a no-op and auditing still works. Visual
# state is driven only by these calls in the runtime, never by model output.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    import hearth_state
except Exception:  # noqa: BLE001
    hearth_state = None


def _emit(agent_id, state, detail, db):
    if hearth_state is None:
        return
    try:
        hearth_state.emit_state(agent_id, state, detail, db=db)
    except Exception:  # never let visual state break a real run
        pass

# The single source of truth for the audit schema. observability.nix calls
# `hearth-agent --init-db` so the schema is defined in exactly one place.
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_runs (
  id          INTEGER PRIMARY KEY,
  agent_name  TEXT,
  run_id      TEXT,
  started_at  TEXT,
  finished_at TEXT,
  tokens_in   INTEGER,
  tokens_out  INTEGER,
  cost_usd    REAL,
  latency_ms  INTEGER,
  error       TEXT,
  model       TEXT
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def init_db(path):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def record_run(path, run):
    init_db(path)
    con = sqlite3.connect(path)
    try:
        con.execute(
            "INSERT INTO agent_runs "
            "(agent_name, run_id, started_at, finished_at, tokens_in, "
            " tokens_out, cost_usd, latency_ms, error, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                run["agent_name"],
                run["run_id"],
                run["started_at"],
                run["finished_at"],
                run["tokens_in"],
                run["tokens_out"],
                run["cost_usd"],
                run["latency_ms"],
                run["error"],
                run["model"],
            ),
        )
        con.commit()
    finally:
        con.close()


def recent_runs(path, limit=20):
    con = sqlite3.connect(path)
    try:
        cur = con.execute(
            "SELECT started_at, agent_name, model, tokens_in, tokens_out, "
            "latency_ms, cost_usd, error FROM agent_runs "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()
    finally:
        con.close()


def call_ollama(base_url, model, prompt, timeout):
    body = json.dumps({"model": model, "prompt": prompt, "stream": False}).encode()
    req = urllib.request.Request(
        base_url.rstrip("/") + "/api/generate",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def run_agent(args):
    run_id = uuid.uuid4().hex
    started = now_iso()
    t0 = time.monotonic()
    error = None
    tokens_in = 0
    tokens_out = 0
    response_text = ""

    _emit(args.agent_name, "SPAWNING", "starting", args.db)
    _emit(args.agent_name, "THINKING", "calling " + args.model, args.db)
    try:
        data = call_ollama(args.ollama_url, args.model, args.prompt, args.timeout)
        response_text = data.get("response", "")
        tokens_in = int(data.get("prompt_eval_count", 0) or 0)
        tokens_out = int(data.get("eval_count", 0) or 0)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        error = "{}: {}".format(type(exc).__name__, exc)

    latency_ms = int((time.monotonic() - t0) * 1000)
    _emit(args.agent_name, "ERRORED" if error else "DONE",
          error or "{} tokens".format(tokens_out), args.db)

    run = {
        "agent_name": args.agent_name,
        "run_id": run_id,
        "started_at": started,
        "finished_at": now_iso(),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        # Local Ollama inference has no per-token API charge, so cost is 0.0.
        # The column stays so hosted models could record real spend later.
        "cost_usd": 0.0,
        "latency_ms": latency_ms,
        "error": error,
        "model": args.model,
    }

    try:
        record_run(args.db, run)
        os.makedirs(args.runs_dir, exist_ok=True)
        record_path = os.path.join(args.runs_dir, run_id + ".json")
        with open(record_path, "w") as handle:
            json.dump(
                dict(run, prompt=args.prompt, response=response_text),
                handle,
                indent=2,
            )
    except OSError as exc:
        print("hearth-agent: failed to record run: {}".format(exc), file=sys.stderr)

    if error:
        print("hearth-agent: run {} failed: {}".format(run_id, error), file=sys.stderr)
        return 1

    print(response_text)
    print(
        "[hearth] run {}: {} in / {} out / {} ms".format(
            run_id, tokens_in, tokens_out, latency_ms
        ),
        file=sys.stderr,
    )
    return 0


def self_test():
    import tempfile

    workdir = tempfile.mkdtemp(prefix="hearth-selftest-")
    db = os.path.join(workdir, "audit.db")
    sample = {
        "agent_name": "selftest",
        "run_id": "deadbeef",
        "started_at": now_iso(),
        "finished_at": now_iso(),
        "tokens_in": 3,
        "tokens_out": 5,
        "cost_usd": 0.0,
        "latency_ms": 42,
        "error": None,
        "model": "test-model",
    }
    record_run(db, sample)
    rows = recent_runs(db, 5)
    assert len(rows) == 1, rows
    assert rows[0][1] == "selftest", rows[0]
    assert rows[0][3] == 3 and rows[0][4] == 5, rows[0]
    print("hearth-agent self-test OK:", rows[0])
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hearth-agent",
        description="Run a prompt against local Ollama and audit the run.",
    )
    parser.add_argument("prompt", nargs="?", help="the prompt text")
    parser.add_argument("--model", default="llama3.2:3b")
    parser.add_argument("--agent-name", default="cli")
    parser.add_argument("--ollama-url", default=DEFAULT_OLLAMA)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--runs-dir", default=DEFAULT_RUNS_DIR)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument(
        "--init-db", action="store_true", help="create the audit schema and exit"
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="exercise the audit database without contacting Ollama",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.init_db:
        init_db(args.db)
        print("hearth-agent: initialized audit schema at {}".format(args.db))
        return 0
    if not args.prompt:
        parser.error("a prompt is required unless --init-db or --self-test is given")
    return run_agent(args)


if __name__ == "__main__":
    sys.exit(main())
