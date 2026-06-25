#!/usr/bin/env python3
"""hearth agent runtime state model and live state store.

This is the single source of truth for agent visual state. The rule the tycoon
UI depends on: visuals are driven ONLY by these runtime states, never by model
output. The agent runtime calls emit_state() at each transition; nothing here or
downstream ever reads an LLM response to choose a visual. Visuals therefore cost
zero model tokens by construction.

States are a closed set. Each maps to one fixed icon (the frontend has the same
table). Storage is the same SQLite database as the audit log, with two tables:
  agent_state   - current state per agent (one row per agent, upserted)
  agent_events  - append-only log of every transition (the event stream source)

Standard library only, so it packages trivially with Nix and is easy to audit.

CLI:
  hearth-state emit <agent_id> <state> [detail]   record one transition
  hearth-state snapshot                           print current state of all agents
  hearth-state sim [n]                            drive n fake agents through a
                                                  realistic state sequence (for
                                                  demos and UI testing, no Ollama)
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")

# The closed set of runtime states. Order is roughly the lifecycle order.
STATES = [
    "SPAWNING",   # process starting, walking onto the map
    "IDLE",       # alive, no task in flight
    "THINKING",   # an LLM call is in flight
    "TOOL_CALL",  # executing a tool / command
    "WAITING_IO", # blocked on external I/O (network, disk, another agent)
    "WAITING_APPROVAL",  # paused, needs the user to approve or deny a tool
    "ERRORED",    # last step failed
    "DONE",       # finished, walking off
]

# Fixed icon per state. The frontend (webui/static/index.html) keeps an
# identical table. Keep these in sync. Emoji are the placeholder icon set for
# milestone 1; real sprite assets can replace them without changing the model.
STATE_ICONS = {
    "SPAWNING": "✨",
    "IDLE": "💤",
    "THINKING": "💭",
    "TOOL_CALL": "🔧",
    "WAITING_IO": "⏳",
    "WAITING_APPROVAL": "✋",
    "ERRORED": "❗",
    "DONE": "✅",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_state (
  agent_id   TEXT PRIMARY KEY,
  state      TEXT NOT NULL,
  detail     TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_events (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  ts       TEXT NOT NULL,
  agent_id TEXT NOT NULL,
  state    TEXT NOT NULL,
  detail   TEXT
);
CREATE TABLE IF NOT EXISTS agent_meta (
  agent_id   TEXT PRIMARY KEY,
  parent_id  TEXT,
  kind       TEXT,
  goal       TEXT,
  created_at TEXT NOT NULL
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _connect(db):
    parent = os.path.dirname(db)
    if parent:
        os.makedirs(parent, exist_ok=True)
    # Default (rollback journal) mode, not WAL. WAL needs a shared -shm file that
    # every writer must write, which is awkward across a DynamicUser agent and
    # the hearth user. With the default journal, only the db file's group-write
    # bit matters (it lives in the setgid, group-writable runs dir). The timeout
    # lets concurrent writers wait for the lock instead of failing immediately.
    con = sqlite3.connect(db, timeout=10)
    return con


def ensure_schema(db=DEFAULT_DB):
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        con.commit()
    finally:
        con.close()


def emit_state(agent_id, state, detail="", db=DEFAULT_DB):
    """Record one runtime transition. Called by the agent runtime only."""
    if state not in STATE_ICONS:
        raise ValueError("unknown state: {} (allowed: {})".format(state, STATES))
    ts = now_iso()
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        con.execute(
            "INSERT INTO agent_state (agent_id, state, detail, updated_at) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(agent_id) DO UPDATE SET "
            "state=excluded.state, detail=excluded.detail, updated_at=excluded.updated_at",
            (agent_id, state, detail, ts),
        )
        con.execute(
            "INSERT INTO agent_events (ts, agent_id, state, detail) VALUES (?, ?, ?, ?)",
            (ts, agent_id, state, detail),
        )
        con.commit()
    finally:
        con.close()
    return ts


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


def snapshot(db=DEFAULT_DB):
    """Current state of every known agent."""
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        cur = con.execute(
            "SELECT agent_id, state, detail, updated_at FROM agent_state "
            "ORDER BY agent_id"
        )
        return [
            {
                "agent_id": r[0],
                "state": r[1],
                "detail": r[2] or "",
                "updated_at": r[3],
                "icon": STATE_ICONS.get(r[1], "?"),
            }
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def events_since(last_id, db=DEFAULT_DB, limit=500):
    """Transitions with id greater than last_id, for the event stream."""
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        con.executescript(SCHEMA)
        cur = con.execute(
            "SELECT id, ts, agent_id, state, detail FROM agent_events "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, limit),
        )
        return [
            {
                "id": r[0],
                "ts": r[1],
                "agent_id": r[2],
                "state": r[3],
                "detail": r[4] or "",
                "icon": STATE_ICONS.get(r[3], "?"),
            }
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def _sim(n, db):
    """Drive n fake agents through a realistic sequence. No Ollama needed."""
    sequence = [
        ("SPAWNING", "starting up"),
        ("IDLE", "ready"),
        ("THINKING", "calling model"),
        ("TOOL_CALL", "running a tool"),
        ("WAITING_IO", "waiting on I/O"),
        ("THINKING", "second pass"),
        ("DONE", "finished"),
    ]
    agents = ["sim-{}".format(i + 1) for i in range(n)]
    for agent in agents:
        emit_state(agent, "SPAWNING", "starting up", db=db)
    # Stagger the agents so the map shows different states at once.
    for step, (state, detail) in enumerate(sequence):
        for a_index, agent in enumerate(agents):
            if step >= a_index:  # later agents start later
                emit_state(agent, state, detail, db=db)
        print("step {}: {}".format(step, state))
        time.sleep(1.0)
    return 0


def _self_test():
    assert "WAITING_APPROVAL" in STATES, "WAITING_APPROVAL missing from STATES"
    assert "WAITING_APPROVAL" in STATE_ICONS, "WAITING_APPROVAL missing from STATE_ICONS"
    import tempfile
    db = os.path.join(tempfile.mkdtemp(prefix="hearth-state-"), "s.db")
    emit_state("a1", "WAITING_APPROVAL", "needs approval: run_command", db=db)
    snap = {r["agent_id"]: r for r in snapshot(db)}
    assert snap["a1"]["state"] == "WAITING_APPROVAL", snap
    import tempfile as _tfm
    mdb = os.path.join(_tfm.mkdtemp(prefix="hearth-meta-"), "m.db")
    record_meta("mgr-1", None, "manager", "build a thing", db=mdb)
    record_meta("mgr-1-s1", "mgr-1", "specialist", "do part one", db=mdb)
    metas = {m["agent_id"]: m for m in read_meta(mdb)}
    assert metas["mgr-1"]["kind"] == "manager" and metas["mgr-1"]["parent_id"] is None, metas
    assert metas["mgr-1-s1"]["parent_id"] == "mgr-1", metas
    print("hearth-state self-test OK")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hearth-state")
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--self-test", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=False)

    p_emit = sub.add_parser("emit", help="record one transition")
    p_emit.add_argument("agent_id")
    p_emit.add_argument("state", choices=STATES)
    p_emit.add_argument("detail", nargs="?", default="")

    sub.add_parser("snapshot", help="print current state of all agents")

    p_sim = sub.add_parser("sim", help="drive fake agents for a demo")
    p_sim.add_argument("n", nargs="?", type=int, default=3)

    args = parser.parse_args(argv)

    if getattr(args, "self_test", False):
        return _self_test()

    if args.cmd is None:
        parser.print_help()
        return 2

    if args.cmd == "emit":
        emit_state(args.agent_id, args.state, args.detail, db=args.db)
        print("emitted {} -> {}".format(args.agent_id, args.state))
        return 0
    if args.cmd == "snapshot":
        print(json.dumps(snapshot(args.db), indent=2))
        return 0
    if args.cmd == "sim":
        return _sim(args.n, args.db)
    return 1


if __name__ == "__main__":
    sys.exit(main())
