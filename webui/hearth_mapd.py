#!/usr/bin/env python3
"""hearth-mapd: the tycoon map backend.

Serves the map web page and streams agent runtime state to it. Reads the live
state that the agent runtime writes via agent/hearth_state.py (tables
agent_state and agent_events in the hearth SQLite database). It never talks to
an LLM and never decides visuals; it only relays runtime state. The browser
maps state to a fixed icon on its side.

Endpoints:
  GET /            the map page (webui/static/index.html)
  GET /state       JSON snapshot of every agent's current state
  GET /events      server-sent events: one message per new transition
  GET /healthz     liveness check

Standard library only (http.server + sqlite3). Run with:
  hearth-mapd --port 8770 --db /var/lib/hearth/runs/audit.db
"""

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
DEFAULT_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
OLLAMA_URL = os.environ.get("HEARTH_OLLAMA", "http://127.0.0.1:11434")

LOCAL_IPS = {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
API_TOKEN = os.environ.get("HEARTH_API_TOKEN", "")

# The cockpit runs as a systemd service with a minimal PATH, so the kill switch
# resolves these tools explicitly: PATH first, then the NixOS stable locations
# (the system profile and the setuid sudo wrapper). On a non-NixOS dev box these
# fall back to paths that do not exist, and the kill switch simply finds no units.
SYSTEMCTL = shutil.which("systemctl") or "/run/current-system/sw/bin/systemctl"
SUDO = shutil.which("sudo") or "/run/wrappers/bin/sudo"
MAX_SESSIONS = 24  # safety cap on concurrent interactive sessions (single-user cockpit)


def request_allowed(client_ip, auth_header, token):
    """Localhost is always allowed (the local cockpit). Remote requests need a
    bearer token matching the configured one. If no token is configured, remote
    access is denied (localhost-only)."""
    if client_ip in LOCAL_IPS:
        return True
    if not token:
        return False
    expected = "Bearer " + token
    return bool(auth_header) and auth_header == expected


RATE_LIMIT = int(os.environ.get("HEARTH_RATE_LIMIT", "120"))  # requests per window
RATE_WINDOW = 60.0  # seconds
_RATE_STORE = {}
_RATE_LOCK = threading.Lock()


def rate_allow(ip, now, store, limit=RATE_LIMIT, window=RATE_WINDOW):
    """Sliding-window rate check. Mutates store[ip] (a list of timestamps).
    Returns True if the request is within the limit. Pure and testable."""
    q = store.setdefault(ip, [])
    cutoff = now - window
    while q and q[0] < cutoff:
        q.pop(0)
    if len(q) >= limit:
        return False
    q.append(now)
    return True

# Created by the agent runtime; defined here too so /state and /events work even
# before any agent has run.
SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_state (
  agent_id TEXT PRIMARY KEY, state TEXT NOT NULL, detail TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS agent_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, agent_id TEXT NOT NULL,
  state TEXT NOT NULL, detail TEXT
);
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


def _connect(db):
    con = sqlite3.connect(db, timeout=10)
    con.executescript(SCHEMA)
    return con


def read_snapshot(db):
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        cur = con.execute(
            "SELECT agent_id, state, detail, updated_at FROM agent_state ORDER BY agent_id"
        )
        return [
            {"agent_id": r[0], "state": r[1], "detail": r[2] or "", "updated_at": r[3]}
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def read_tree(db):
    """Lineage nodes (agent_meta) joined with current state, for the mission map."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        con.execute("CREATE TABLE IF NOT EXISTS agent_meta ("
                    "agent_id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, goal TEXT, created_at TEXT)")
        cur = con.execute(
            "SELECT m.agent_id, m.parent_id, m.kind, m.goal, m.created_at, s.state, s.detail "
            "FROM agent_meta m LEFT JOIN agent_state s ON s.agent_id = m.agent_id "
            "ORDER BY m.created_at")
        cols = ["agent_id", "parent_id", "kind", "goal", "created_at", "state", "detail"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []


def read_events_since(db, last_id, limit=500):
    if not os.path.exists(db):
        return []
    con = _connect(db)
    try:
        cur = con.execute(
            "SELECT id, ts, agent_id, state, detail FROM agent_events "
            "WHERE id > ? ORDER BY id LIMIT ?",
            (last_id, limit),
        )
        return [
            {"id": r[0], "ts": r[1], "agent_id": r[2], "state": r[3], "detail": r[4] or ""}
            for r in cur.fetchall()
        ]
    finally:
        con.close()


def max_event_id(db):
    if not os.path.exists(db):
        return 0
    con = _connect(db)
    try:
        (mx,) = con.execute("SELECT COALESCE(MAX(id), 0) FROM agent_events").fetchone()
        return int(mx)
    finally:
        con.close()


def parse_gpu(csv_text):
    """Parse one line of: nvidia-smi --query-gpu=name,utilization.gpu,
    memory.used,memory.total --format=csv,noheader,nounits"""
    line = (csv_text or "").strip().splitlines()
    if not line:
        return None
    parts = [p.strip() for p in line[0].split(",")]
    if len(parts) < 4:
        return None
    try:
        return {
            "name": parts[0],
            "util_pct": int(float(parts[1])),
            "mem_used_mb": int(float(parts[2])),
            "mem_total_mb": int(float(parts[3])),
        }
    except ValueError:
        return None


def parse_meminfo(text):
    """Parse /proc/meminfo into used/total MB."""
    vals = {}
    for ln in (text or "").splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            vals[k.strip()] = v.strip()

    def kb(key):
        try:
            return int(vals.get(key, "0").split()[0])
        except (ValueError, IndexError):
            return 0

    total = kb("MemTotal")
    available = kb("MemAvailable")
    used = max(total - available, 0)
    return {"used_mb": used // 1024, "total_mb": total // 1024}


def parse_models(tags_json_text):
    """Extract model names from an Ollama /api/tags JSON body."""
    try:
        data = json.loads(tags_json_text)
    except (ValueError, TypeError):
        return []
    return [m.get("name", "") for m in data.get("models", []) if m.get("name")]


def read_stats():
    gpu = None
    if shutil.which("nvidia-smi"):
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=name,utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout
            gpu = parse_gpu(out)
        except (OSError, subprocess.SubprocessError):
            gpu = None
    mem = None
    try:
        with open("/proc/meminfo") as fh:
            mem = parse_meminfo(fh.read())
    except OSError:
        mem = None
    return {"gpu": gpu, "mem": mem}


def read_models():
    try:
        url = OLLAMA_URL.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            return parse_models(resp.read().decode())
    except (urllib.error.URLError, OSError, ValueError):
        return []


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


def read_runs(db, limit=20):
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        cur = con.execute(
            "SELECT started_at, agent_name, model, tokens_in, tokens_out, "
            "latency_ms, cost_usd, error FROM agent_runs ORDER BY started_at DESC LIMIT ?",
            (limit,))
        cols = ["started_at", "agent_name", "model", "tokens_in", "tokens_out",
                "latency_ms", "cost_usd", "error"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []


# What the same tokens would have cost on a frontier cloud model, blended
# input+output, in dollars per million tokens. Deliberately a round conservative
# figure; it powers the "cloud cost saved" counter, not billing.
CLOUD_PRICE_PER_MTOK = float(os.environ.get("HEARTH_CLOUD_PRICE_MTOK", "15.0"))


def cloud_saved_usd(tokens):
    """Estimate what `tokens` would have cost on a frontier cloud model."""
    return round((tokens or 0) / 1_000_000.0 * CLOUD_PRICE_PER_MTOK, 2)


def read_stats_history(db, days=14):
    """Aggregate the audit log over time: runs/tokens/cost per day, per model, and
    grand totals. Powers the cockpit stats view."""
    empty = {"by_day": [], "by_model": [],
             "totals": {"runs": 0, "tokens": 0, "cost": 0, "errors": 0, "saved_usd": 0.0}}
    if not os.path.exists(db):
        return empty
    try:
        con = sqlite3.connect(db, timeout=10)
        con.executescript(SCHEMA)
        by_day = con.execute(
            "SELECT substr(started_at,1,10) d, COUNT(*), "
            "COALESCE(SUM(tokens_in+tokens_out),0), COALESCE(SUM(cost_usd),0) "
            "FROM agent_runs GROUP BY d ORDER BY d DESC LIMIT ?", (days,)).fetchall()
        by_model = con.execute(
            "SELECT COALESCE(model,'?'), COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0) "
            "FROM agent_runs GROUP BY model ORDER BY 2 DESC LIMIT 10").fetchall()
        tot = con.execute(
            "SELECT COUNT(*), COALESCE(SUM(tokens_in+tokens_out),0), COALESCE(SUM(cost_usd),0), "
            "COALESCE(SUM(CASE WHEN error IS NOT NULL AND error!='' THEN 1 ELSE 0 END),0) "
            "FROM agent_runs").fetchone()
        con.close()
    except sqlite3.Error:
        return empty
    return {
        "by_day": [{"day": r[0], "runs": r[1], "tokens": r[2], "cost": round(r[3] or 0, 4),
                    "saved_usd": cloud_saved_usd(r[2])}
                   for r in reversed(by_day)],
        "by_model": [{"model": r[0], "runs": r[1], "tokens": r[2]} for r in by_model],
        "totals": {"runs": tot[0], "tokens": tot[1], "cost": round(tot[2] or 0, 4), "errors": tot[3],
                   "saved_usd": cloud_saved_usd(tot[1])},
    }


def read_budget(db):
    """The v1.5 spend circuit breaker as the cockpit sees it: today's token
    spend (tokens_in + tokens_out over agent_runs, UTC day) against the daily
    cap from HEARTH_DAILY_TOKEN_CAP. cap 0 or unset means no cap. When the cap
    is reached the agent loop refuses new runs (recording the error
    "budget: daily token cap reached"), so capped=True means the breaker is
    open."""
    try:
        cap = int(os.environ.get("HEARTH_DAILY_TOKEN_CAP", "0") or "0")
    except ValueError:
        cap = 0
    tokens = 0
    runs = 0
    if os.path.exists(db):
        try:
            con = sqlite3.connect(db, timeout=10)
            con.executescript(SCHEMA)
            day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = con.execute(
                "SELECT COUNT(*), COALESCE(SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)),0) "
                "FROM agent_runs WHERE substr(started_at,1,10)=?", (day,)).fetchone()
            runs, tokens = int(row[0]), int(row[1])
            con.close()
        except sqlite3.Error:
            pass
    return {"cap": cap, "tokens_today": tokens, "runs_today": runs,
            "remaining": max(0, cap - tokens), "capped": cap > 0 and tokens >= cap}


def read_egress(db, agent="", limit=200, blocked=False):
    """Read the egress log (outbound network attempts recorded by the agent
    tools, or by the OS-layer watcher with tool='os'), newest first, optionally
    for one agent and optionally blocked (allowed=0) rows only. limit is capped
    at 200 so the world HUD can poll cheaply."""
    if not os.path.exists(db):
        return []
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        limit = 200
    limit = max(1, min(limit, 200))
    try:
        con = sqlite3.connect(db, timeout=10)
        where, params = [], []
        if agent:
            where.append("agent_id=?")
            params.append(agent)
        if blocked:
            where.append("allowed=0")
        q = "SELECT id, agent_id, ts, tool, host, url, allowed FROM egress_log"
        if where:
            q += " WHERE " + " AND ".join(where)
        q += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        cur = con.execute(q, tuple(params))
        rows = [{"id": r[0], "agent": r[1], "ts": r[2], "tool": r[3],
                 "host": r[4], "url": r[5], "allowed": bool(r[6])}
                for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []


def read_tripwires(db, limit=100):
    """Read tripwire hits (honeyfile decoy reads), newest first."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        if not _table_exists(con, "tripwires"):
            con.close()
            return []
        cur = con.execute(
            "SELECT agent_id, ts, tool, path, token, detail FROM tripwires "
            "ORDER BY id DESC LIMIT ?", (limit,))
        rows = [{"agent": r[0], "ts": r[1], "tool": r[2], "path": r[3],
                 "token": r[4], "detail": r[5]} for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []


# Step-by-step run log written by the agent loop; defined here too so the
# replay endpoints work even before any instrumented run has happened.
REPLAY_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS run_steps (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "agent_id TEXT, ts TEXT, seq INTEGER, kind TEXT, tool TEXT, args TEXT, "
    "output TEXT, duration_ms INTEGER, verdict TEXT)")


def read_replay_agents(db, limit=50):
    """Distinct agents in the step log, newest activity first, with step and
    tool counts plus the kind of the final step (done/error/tripwire) so the
    replay picker can show how each run ended."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.execute(REPLAY_SCHEMA)
        cur = con.execute(
            "SELECT agent_id, COUNT(*), "
            "COALESCE(SUM(CASE WHEN kind='tool' THEN 1 ELSE 0 END),0), "
            "MIN(ts), MAX(ts) FROM run_steps GROUP BY agent_id "
            "ORDER BY MAX(ts) DESC LIMIT ?", (limit,))
        rows = []
        for r in cur.fetchall():
            last = con.execute(
                "SELECT kind FROM run_steps WHERE agent_id=? ORDER BY seq DESC LIMIT 1",
                (r[0],)).fetchone()
            rows.append({"agent_id": r[0], "steps": r[1], "tools": r[2],
                         "first_ts": r[3], "last_ts": r[4],
                         "last_kind": (last[0] if last else "") or ""})
        con.close()
        return rows
    except sqlite3.Error:
        return []


def read_replay_steps(db, agent, limit=2000):
    """Every recorded step for one agent, in execution order, for the replay
    timeline."""
    if not os.path.exists(db):
        return []
    try:
        con = sqlite3.connect(db, timeout=10)
        con.execute(REPLAY_SCHEMA)
        cur = con.execute(
            "SELECT seq, ts, kind, tool, args, output, duration_ms, verdict "
            "FROM run_steps WHERE agent_id=? ORDER BY seq ASC LIMIT ?",
            (agent, limit))
        cols = ["seq", "ts", "kind", "tool", "args", "output", "duration_ms", "verdict"]
        rows = [dict(zip(cols, r)) for r in cur.fetchall()]
        con.close()
        return rows
    except sqlite3.Error:
        return []


def _table_exists(con, name):
    try:
        return con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def read_security(db, is_active_fn=None):
    """One security scoreboard for the cockpit: what containment is active on
    this box right now, plus egress/tripwire activity counts."""
    if is_active_fn is None:
        def is_active_fn(u):
            try:
                r = subprocess.run([SYSTEMCTL, "is-active", u], capture_output=True, text=True, timeout=6)
                return (r.stdout or "").strip() == "active"
            except (OSError, subprocess.SubprocessError):
                return False
    egress_total = egress_blocked = 0
    tripwires_armed = False
    tripwire_count = 0
    if os.path.exists(db):
        try:
            con = sqlite3.connect(db, timeout=10)
            if _table_exists(con, "egress_log"):
                row = con.execute(
                    "SELECT COUNT(*), COALESCE(SUM(CASE WHEN allowed=0 THEN 1 ELSE 0 END),0) "
                    "FROM egress_log").fetchone()
                egress_total, egress_blocked = row[0], row[1]
            if _table_exists(con, "tripwires"):
                tripwires_armed = True
                tripwire_count = con.execute("SELECT COUNT(*) FROM tripwires").fetchone()[0]
            con.close()
        except sqlite3.Error:
            pass
    budget = read_budget(db)
    return {
        "remote_auth": bool(API_TOKEN),
        "rate_limit_per_min": RATE_LIMIT,
        "manifests_supported": True,
        # v1.5 "Governor": the spend circuit breaker and whether an alert
        # channel is wired up (telegram via env, ntfy via topic).
        "budget": {"cap": budget["cap"], "capped": budget["capped"]},
        "alerting": {"telegram_env": bool(os.environ.get("TELEGRAM_BOT_TOKEN")),
                     "ntfy": bool(os.environ.get("HEARTH_NTFY_TOPIC"))},
        # v1.4 "Wall": True when the NixOS module has per-run nftables egress
        # enforcement switched on for this box (env set on the mapd service).
        "egress_os": os.environ.get("HEARTH_EGRESS_OS") == "1",
        "egress": {"logged": egress_total, "blocked": egress_blocked},
        "tripwires": {"armed": tripwires_armed, "trips": tripwire_count},
        "daemons": {u: is_active_fn(u) for u in
                    ("hearth-mapd.service", "hearth-grow.service", "hearth-schedule.timer")},
    }


def read_tools():
    """The agent tool registry for the cockpit launch panel: [{name, description,
    risk}]. The agent modules live in a sibling dir in the repo, or wherever
    HEARTH_AGENT_DIR points on a deployed box; when neither is importable this
    returns [] and the cockpit falls back to a plain text field."""
    import sys as _sys
    candidates = [os.environ.get("HEARTH_AGENT_DIR", ""),
                  os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")]
    for d in candidates:
        if d and os.path.isdir(d) and d not in _sys.path:
            _sys.path.insert(0, d)
    try:
        import hearth_tools as _ht
        import permissions as _pm
        return [{"name": t["name"], "description": t["description"],
                 "risk": _pm.risk_of(t["name"])} for t in _ht.TOOLS]
    except Exception:  # noqa: BLE001 - the registry is a nicety, never a crash
        return []


def _ensure_agent_path():
    """Put the agent module directory on sys.path so the agent-side helpers
    (hearth_router, hearth_askdb, ...) import, matching read_tools()'s lookup:
    HEARTH_AGENT_DIR first, then the sibling agent/ dir in the repo."""
    import sys as _sys
    for d in (os.environ.get("HEARTH_AGENT_DIR", ""),
              os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "agent")):
        if d and os.path.isdir(d) and d not in _sys.path:
            _sys.path.insert(0, d)


def read_router():
    """The active router ruleset (hearth_router.load_rules) plus the resolved
    rules-file path, for GET /router. Best-effort: any import or load failure
    yields an empty ruleset so the view never crashes."""
    rpath = os.environ.get("HEARTH_ROUTER", "/etc/hearth/router.json")
    rules = {"default": "", "rules": []}
    _ensure_agent_path()
    try:
        import hearth_router as _hr
        loaded = _hr.load_rules(rpath)
        if isinstance(loaded, dict):
            rules = loaded
    except Exception:  # noqa: BLE001 - the router view is a nicety, never a crash
        pass
    return {"default": rules.get("default", ""),
            "rules": rules.get("rules", []), "path": rpath}


# Test/inject seam for POST /ask: when {"fn": callable} is set, the /ask handler
# uses it as the chat_fn instead of building one that calls the local model. Kept
# a one-key dict so tests can set it without a `global` declaration.
_ASK_CHAT_HOOK = {"fn": None}


def _prom_label(v):
    return str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def prometheus_metrics(db, units=("hearth-grow.service", "hearth-mapd.service",
                                  "hearth-schedule.timer"), is_active_fn=None):
    """Render hearth's stats in Prometheus exposition format so any standard
    scraper (Grafana, etc.) can graph runs, tokens, errors, and daemon health."""
    h = read_stats_history(db)
    t = h["totals"]
    if is_active_fn is None:
        def is_active_fn(u):
            try:
                r = subprocess.run([SYSTEMCTL, "is-active", u], capture_output=True, text=True, timeout=6)
                return (r.stdout or "").strip() == "active"
            except (OSError, subprocess.SubprocessError):
                return False
    L = []
    L += ["# HELP hearth_runs_total Total agent runs recorded.",
          "# TYPE hearth_runs_total counter", "hearth_runs_total {}".format(t["runs"])]
    L += ["# HELP hearth_tokens_total Total tokens across all runs.",
          "# TYPE hearth_tokens_total counter", "hearth_tokens_total {}".format(t["tokens"])]
    L += ["# HELP hearth_errors_total Total runs that ended in error.",
          "# TYPE hearth_errors_total counter", "hearth_errors_total {}".format(t["errors"])]
    L += ["# HELP hearth_runs_by_model Runs per model.", "# TYPE hearth_runs_by_model counter"]
    for m in h["by_model"]:
        L.append('hearth_runs_by_model{{model="{}"}} {}'.format(_prom_label(m["model"]), m["runs"]))
    L += ["# HELP hearth_daemon_up 1 if the unit is active, else 0.",
          "# TYPE hearth_daemon_up gauge"]
    for u in units:
        L.append('hearth_daemon_up{{unit="{}"}} {}'.format(_prom_label(u), 1 if is_active_fn(u) else 0))
    return "\n".join(L) + "\n"


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
    """Mark a pending action allow/deny so the waiting worker proceeds. Returns
    True only if a matching pending row was actually updated (a missing/unknown id
    returns False rather than a misleading success)."""
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(PENDING_SCHEMA)
            cur = con.execute("UPDATE pending_actions SET decision=? WHERE id=?",
                              ("allow" if allow else "deny", action_id))
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()
    except sqlite3.Error:
        return False


def deny_all_pending(db):
    """Deny every still-undecided approval request (used by the kill switch so a
    stopped worker's request does not linger in the queue). Returns the count."""
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            con.executescript(PENDING_SCHEMA)
            cur = con.execute("UPDATE pending_actions SET decision='deny' WHERE decision IS NULL")
            con.commit()
            return cur.rowcount
        finally:
            con.close()
    except sqlite3.Error:
        return 0


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


GROW_REPO = "/var/lib/hearth/grow-repo"


def read_growth(db, repo=GROW_REPO, limit=40):
    """The self-improvement ledger: recent grow lessons (what hearth tried and
    whether it validated), the validated branches waiting for review, and whether
    the always-on growth daemon is running. Pure reads; never raises."""
    daemon = "unknown"
    try:
        r = subprocess.run([SYSTEMCTL, "is-active", "hearth-grow.service"],
                           capture_output=True, text=True, timeout=8)
        daemon = (r.stdout or r.stderr or "unknown").strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        daemon = "unknown"

    lessons = []
    validated = 0
    if os.path.exists(db):
        try:
            con = sqlite3.connect(db, timeout=10)
            try:
                con.execute(
                    "CREATE TABLE IF NOT EXISTS learnings (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " ts TEXT, kind TEXT, topic TEXT, insight TEXT, tags TEXT, source TEXT)")
                cur = con.execute(
                    "SELECT ts, kind, insight FROM learnings WHERE source='grow' "
                    "ORDER BY id DESC LIMIT ?", (limit,))
                for ts, kind, insight in cur.fetchall():
                    lessons.append({"ts": ts, "kind": kind or "lesson", "insight": insight or ""})
                validated = con.execute(
                    "SELECT COUNT(*) FROM learnings WHERE source='grow' AND kind='success'"
                ).fetchone()[0]
            finally:
                con.close()
        except sqlite3.Error:
            pass

    branches = []
    merged = 0
    git = shutil.which("git") or "/run/current-system/sw/bin/git"
    try:
        r = subprocess.run([git, "-C", repo, "branch", "--list", "hearth-evolve-*",
                            "--format=%(refname:short)"], capture_output=True, text=True, timeout=8)
        branches = [b.strip() for b in (r.stdout or "").splitlines() if b.strip()]
    except (OSError, subprocess.SubprocessError):
        branches = []
    try:
        # Improvements that compounded: merge commits the growth loop made on main.
        r = subprocess.run([git, "-C", repo, "log", "main", "--grep=grow: merge", "--oneline"],
                          capture_output=True, text=True, timeout=8)
        merged = len([x for x in (r.stdout or "").splitlines() if x.strip()])
    except (OSError, subprocess.SubprocessError):
        merged = 0

    return {"daemon": daemon, "validated_count": validated, "merged_count": merged,
            "lessons": lessons, "branches": branches}


SCHEDULE_REGISTRY = "/var/lib/hearth/scheduler/schedule.json"
DEFAULT_MISSIONS = "/etc/hearth/missions.json"


def _read_registry(path=SCHEDULE_REGISTRY):
    """The raw mutable registry file, exactly as stored (no source marks, no
    declarative entries). The write paths go through this so nix-declared
    missions can never leak into the registry file."""
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


def read_declared_missions(path=None):
    """Missions declared in the nix config: env HEARTH_MISSIONS points at the
    JSON list the module writes (default /etc/hearth/missions.json). Entries are
    shaped like registry missions with id 'nix-{name}' and source 'nix'; the
    scheduler runs them under the same ids. A missing or invalid file simply
    means no declared missions."""
    if path is None:
        path = os.environ.get("HEARTH_MISSIONS", DEFAULT_MISSIONS)
    try:
        with open(path) as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(d, list):
        return []
    out = []
    for m in d:
        if not isinstance(m, dict) or not m.get("name"):
            continue
        e = dict(m)
        e["id"] = "nix-{}".format(m["name"])
        e["source"] = "nix"
        e["enabled"] = m.get("enabled", True)
        e.setdefault("last_run", None)
        out.append(e)
    return out


def read_schedule(path=SCHEDULE_REGISTRY):
    """Every standing mission the cockpit should show: the mutable registry
    (source 'user', editable) followed by the nix-declared missions (source
    'nix', read-only here; edit the config and rebuild to change them)."""
    missions = []
    for m in _read_registry(path):
        e = dict(m)
        e["source"] = "user"
        missions.append(e)
    missions.extend(read_declared_missions())
    return missions


def write_schedule(missions, path=SCHEDULE_REGISTRY):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(missions, fh, indent=2)
    os.replace(tmp, path)


def schedule_add(req, path=SCHEDULE_REGISTRY):
    """Add a standing mission from a request dict. Returns (id, error)."""
    sched = {}
    if req.get("every_minutes"):
        try:
            sched = {"every_minutes": int(req["every_minutes"])}
        except (ValueError, TypeError):
            sched = {}
    elif req.get("at"):
        sched = {"at": str(req["at"])[:5]}
    goal = (req.get("goal") or "").strip()
    if not goal or not sched:
        return None, "goal and a schedule (every_minutes or at HH:MM) are required"
    m = {"id": "m-" + uuid.uuid4().hex[:8], "name": (req.get("name") or "mission")[:60],
         "goal": goal, "model": req.get("model") or "qwen2.5-coder",
         "mode": req.get("mode") or "bypass", "kind": req.get("kind") or "agent",
         "schedule": sched, "enabled": True, "last_run": None}
    missions = _read_registry(path)
    missions.append(m)
    write_schedule(missions, path)
    return m["id"], ""


def schedule_remove(mid, path=SCHEDULE_REGISTRY):
    missions = _read_registry(path)
    kept = [m for m in missions if m.get("id") != mid]
    if len(kept) != len(missions):
        write_schedule(kept, path)
        return True
    return False


def schedule_toggle(mid, path=SCHEDULE_REGISTRY):
    missions = _read_registry(path)
    found = False
    for m in missions:
        if m.get("id") == mid:
            m["enabled"] = not m.get("enabled", True)
            found = True
    if found:
        write_schedule(missions, path)
    return found


def kick_spawn():
    """Self-heal the queue watcher and actively process the queue. The
    hearth-spawn.path unit can die into a 'failed' state and then silently
    swallow every launch (queue files pile up, nothing spawns). After dropping a
    queue file we (1) clear any failed state, (2) start hearth-spawn.service
    directly so this run starts even if the path unit never fires, and (3)
    re-arm the path unit for future launches. Best-effort; never raises."""
    for args in (
        ["reset-failed", "hearth-spawn.path", "hearth-spawn.service"],
        ["start", "--no-block", "hearth-spawn.service"],
        ["start", "hearth-spawn.path"],
    ):
        try:
            subprocess.run([SUDO, "-n", SYSTEMCTL] + args, capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.SubprocessError):
            pass


def grow_daemon_action(action):
    """Start or stop the always-on growth daemon. Returns (ok, detail)."""
    if action not in ("start", "stop", "restart"):
        return False, "bad action"
    try:
        r = subprocess.run([SUDO, "-n", SYSTEMCTL, action, "hearth-grow.service"],
                           capture_output=True, text=True, timeout=20)
        return r.returncode == 0, (r.stderr or r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


LIVE_REPO = "/home/operator/hearth-desktop"
PROMOTE_UNIT = "hearth-promote.service"
PROMOTE_STAGE = "/var/lib/hearth/promote-stage"
PROMOTE_HISTORY = "/var/lib/hearth/promote-history.tsv"
NIXOS_REBUILD = shutil.which("nixos-rebuild") or "/run/current-system/sw/bin/nixos-rebuild"
SYSTEMD_RUN = shutil.which("systemd-run") or "/run/current-system/sw/bin/systemd-run"
JOURNALCTL = shutil.which("journalctl") or "/run/current-system/sw/bin/journalctl"
DIFF = shutil.which("diff") or "/run/current-system/sw/bin/diff"
GIT = shutil.which("git") or "/run/current-system/sw/bin/git"
TAR = shutil.which("tar") or "/run/current-system/sw/bin/tar"


def promote_diff(grow_repo=GROW_REPO, live=LIVE_REPO, max_bytes=60000):
    """Unified diff of what promoting would change in the live config: the grow
    repo's compounded main branch vs the live config files. Exports main to a temp
    dir (mapd runs as operator, who owns the repo) and diffs. Read-only."""
    import tempfile
    tmp = tempfile.mkdtemp(prefix="hearth-pdiff-")
    try:
        ar = subprocess.run([GIT, "-C", grow_repo, "archive", "main"],
                            capture_output=True, timeout=20)
        if ar.returncode != 0:
            return "diff unavailable: " + (ar.stderr.decode("utf-8", "replace")[:200] or "no main branch")
        ex = subprocess.run([TAR, "-x", "-C", tmp], input=ar.stdout, capture_output=True, timeout=20)
        if ex.returncode != 0:
            return "diff unavailable: extract failed"
        r = subprocess.run([DIFF, "-ruN", "--exclude=.hearth-seed-hash", "--exclude=result",
                            "--exclude=__pycache__", "--exclude=*.pyc", "--exclude=.git",
                            live, tmp], capture_output=True, text=True, timeout=25)
        out = (r.stdout or "").replace(tmp, "main")
    except (OSError, subprocess.SubprocessError) as exc:
        return "diff failed: {}".format(exc)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    if not out.strip():
        return "(no differences: the live config already matches the compounded main)"
    return out[:max_bytes] + ("\n...(truncated)" if len(out) > max_bytes else "")


def promote_status():
    """State of the most recent promote action (the transient hearth-promote unit)."""
    info = {"running": False, "result": "", "tail": ""}
    try:
        r = subprocess.run([SYSTEMCTL, "show", PROMOTE_UNIT,
                            "-p", "ActiveState", "-p", "Result"],
                           capture_output=True, text=True, timeout=8)
        props = dict(x.split("=", 1) for x in (r.stdout or "").splitlines() if "=" in x)
        info["running"] = props.get("ActiveState") in ("active", "activating")
        if props.get("ActiveState") and props.get("ActiveState") != "inactive":
            info["result"] = "{} / {}".format(props.get("ActiveState"), props.get("Result", "?"))
        elif props.get("Result"):
            info["result"] = props.get("Result")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        j = subprocess.run([JOURNALCTL, "-u", PROMOTE_UNIT, "--no-pager", "-n", "12", "-o", "cat"],
                           capture_output=True, text=True, timeout=8)
        info["tail"] = (j.stdout or "")[-1500:]
    except (OSError, subprocess.SubprocessError):
        pass
    return info


def promote_run(mode):
    """Run a promote action as a transient systemd unit (so it survives a mapd
    restart). build = prove the grow-repo config builds into a real system
    closure (no activation, the safe default). switch = sync grow-repo into the
    live config and activate it (NixOS keeps the prior generation for rollback).
    rollback = switch back to the previous generation. Returns (ok, detail)."""
    if mode not in ("build", "switch", "rollback"):
        return False, "bad mode"
    if promote_status()["running"]:
        return False, "a promote action is already running"
    # Export the compounded main branch to a clean stage (the unit runs as root,
    # so trust the operator-owned repo for git; nix reads the stage as a plain
    # path so libgit2 never sees the ownership mismatch).
    stage = ("rm -rf {s}; mkdir -p {s}; "
             "git -c safe.directory='*' -C {g} archive main | tar -x -C {s}").format(
                 s=PROMOTE_STAGE, g=GROW_REPO)
    if mode == "build":
        body = stage + "; {nrb} build --flake path:{s}#blade".format(nrb=NIXOS_REBUILD, s=PROMOTE_STAGE)
    elif mode == "switch":
        # Copy the staged main over the live config and activate from live (the
        # live dir stays the source of truth; NixOS keeps the prior generation).
        # The switch step is under set -e, so a build failure aborts cleanly with
        # nothing activated. After a successful activation a WATCHDOG runs (this
        # unit is independent of mapd/the network, so it survives even if the new
        # config breaks them): it waits for services to settle and checks the
        # critical units that guard access (ssh, network, cockpit). If any is
        # down, it auto-rolls-back to the prior generation; if all are up it makes
        # the growth daemon reseed on the new live config.
        body = (stage + "; cp -a {s}/. {l}/; {nrb} switch --flake path:{l}#blade; "
                "set +e; sleep 12; ok=1; "
                "for u in sshd.service NetworkManager.service hearth-mapd.service; do "
                "systemctl is-active --quiet \"$u\" || ok=0; done; "
                "if [ \"$ok\" = 1 ]; then echo 'health check passed'; "
                "systemctl restart hearth-grow.service; exit 0; "
                "else echo 'POST-SWITCH HEALTH CHECK FAILED -> rolling back to previous generation'; "
                "{nrb} switch --rollback; exit 3; fi").format(
            s=PROMOTE_STAGE, l=LIVE_REPO, nrb=NIXOS_REBUILD)
    else:  # rollback
        body = "{nrb} switch --rollback".format(nrb=NIXOS_REBUILD)
    # Run the body under set -e (so any step's failure aborts), then ALWAYS record
    # the outcome to the promote history, then exit with the body's real code.
    inner = ("export PATH=/run/current-system/sw/bin:$PATH; ( set -e; {body} ); rc=$?; "
             "printf '%s\\t%s\\t%s\\n' \"$(date -Is 2>/dev/null)\" {mode} \"$rc\" >> {hist} 2>/dev/null; "
             "exit $rc").format(body=body, mode=mode, hist=PROMOTE_HISTORY)
    subprocess.run([SUDO, "-n", SYSTEMCTL, "reset-failed", PROMOTE_UNIT],
                   capture_output=True, text=True)
    try:
        r = subprocess.run([SUDO, "-n", SYSTEMD_RUN, "--unit=hearth-promote",
                            "--property=Type=oneshot", "/bin/sh", "-c", inner],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0, (r.stderr or r.stdout or "started").strip()[:300]
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)


def read_promote_history(limit=12):
    """Recent promote actions (mode + outcome + time), newest first. Each line is
    'iso_ts<TAB>mode<TAB>rc' appended by the promote unit when it finishes."""
    if not os.path.exists(PROMOTE_HISTORY):
        return []
    try:
        with open(PROMOTE_HISTORY, "r") as fh:
            lines = [l.strip() for l in fh if l.strip()]
    except OSError:
        return []
    out = []
    for l in lines[-limit:]:
        parts = l.split("\t")
        if len(parts) >= 3:
            out.append({"ts": parts[0], "mode": parts[1],
                        "ok": parts[2] == "0", "rc": parts[2]})
    out.reverse()
    return out


def chat_once(base_url, model, messages, timeout=300):
    """Call Ollama /api/chat (non-streaming). Returns (reply_text, tokens_in, tokens_out)."""
    body = json.dumps({"model": model, "messages": messages, "stream": False}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    reply = (data.get("message") or {}).get("content", "")
    return reply, int(data.get("prompt_eval_count", 0) or 0), int(data.get("eval_count", 0) or 0)


def run_diff(db, prompt, model_a, model_b, chat_fn=None, base_url=None):
    """Run one prompt against two models sequentially, timing each, and record
    both turns to the audit log under the 'diff' agent. A failed side carries an
    error instead of an output; the other side still answers. chat_fn is
    injectable for tests (defaults to the real Ollama call)."""
    if chat_fn is None:
        chat_fn = chat_once
    if base_url is None:
        base_url = OLLAMA_URL
    messages = [{"role": "user", "content": prompt}]
    out = {"prompt": prompt}
    for key, model in (("a", model_a), ("b", model_b)):
        t0 = time.monotonic()
        error, reply, tin, tout = None, "", 0, 0
        try:
            reply, tin, tout = chat_fn(base_url, model, messages)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = "{}: {}".format(type(exc).__name__, exc)
        latency = int((time.monotonic() - t0) * 1000)
        _record_chat_run(db, "diff", model, tin, tout, latency, error)
        if error:
            out[key] = {"model": model, "error": error}
        else:
            out[key] = {"model": model, "output": reply,
                        "tokens": tin + tout, "latency_ms": latency}
    return out


def chat_stream(base_url, model, messages, timeout=300):
    """Yield (content_delta, done, prompt_tokens, eval_tokens) from Ollama's
    streaming chat, so the OpenAI endpoint can forward real tokens as they arrive."""
    body = json.dumps({"model": model, "messages": messages, "stream": True}).encode()
    req = urllib.request.Request(base_url.rstrip("/") + "/api/chat", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for line in resp:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            yield ((d.get("message") or {}).get("content", ""), bool(d.get("done")),
                   int(d.get("prompt_eval_count", 0) or 0), int(d.get("eval_count", 0) or 0))


def openai_completion(model, reply, tin, tout, created, cid):
    """Shape a non-streaming OpenAI /v1/chat/completions response body."""
    return {
        "id": cid, "object": "chat.completion", "created": created, "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": reply},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": tin, "completion_tokens": tout, "total_tokens": tin + tout},
    }


def openai_chunk(model, created, cid, delta, finish=None):
    """One OpenAI streaming chunk (chat.completion.chunk)."""
    return {"id": cid, "object": "chat.completion.chunk", "created": created, "model": model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def openai_models(models, created):
    """Shape an OpenAI /v1/models list from local model ids."""
    return {"object": "list", "data": [
        {"id": m, "object": "model", "created": created, "owned_by": "hearth"} for m in models]}


class Session:
    """One interactive agent run: a `hearth-loop --session` child process whose
    stdout JSON events are pumped into an in-memory buffer by a reader thread, and
    whose stdin receives JSON control commands. Thread-safe."""

    def __init__(self, sid, proc):
        self.sid = sid
        self.proc = proc
        self.events = []
        self.lock = threading.Lock()
        self._send_lock = threading.Lock()
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
        """Write one control command to the child's stdin. Thread-safe so two
        callers (e.g. a stop racing a user message) cannot interleave JSON frames.
        Returns False if the child's stdin is already gone."""
        line = json.dumps(cmd) + "\n"  # a serialization error is a caller bug; let it raise
        with self._send_lock:
            try:
                self.proc.stdin.write(line)
                self.proc.stdin.flush()
                return True
            except (BrokenPipeError, OSError):
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
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()


# Process-wide registry of live sessions, keyed by session id.
SESSIONS = {}
SESSIONS_LOCK = threading.Lock()


def _session_env(allowed_creds="", allowed_tools="", allowed_hosts=""):
    """Build the child environment for a session: the mapd env plus the run's
    per-run scoping (credential allowlist, capability manifest, egress
    allowlist). Empty values set nothing (unrestricted, back-compat)."""
    env = dict(os.environ)
    if allowed_creds:
        env["HEARTH_ALLOWED_CREDS"] = allowed_creds
    if allowed_tools:
        env["HEARTH_ALLOWED_TOOLS"] = allowed_tools
    if allowed_hosts:
        env["HEARTH_ALLOWED_HOSTS"] = allowed_hosts
    return env


def spawn_session(loop_cmd, sid, model, mode, workspace, db, ollama_url, allowed_creds="",
                  allowed_tools="", allowed_hosts=""):
    """Start a hearth-loop --session child and wrap it in a Session. The caller
    registers the returned Session in SESSIONS. allowed_creds (comma-separated)
    scopes which stored credentials the agent may read; allowed_tools is the
    run's capability manifest; allowed_hosts its egress allowlist. Empty means
    unrestricted (back-compat)."""
    os.makedirs(workspace, exist_ok=True)
    args = [loop_cmd, "--session", "--model", model, "--mode", mode,
            "--agent-name", sid, "--workspace", workspace, "--db", db,
            "--ollama-url", ollama_url]
    env = _session_env(allowed_creds, allowed_tools, allowed_hosts)
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, bufsize=1, env=env)
    return Session(sid, proc)


class Handler(BaseHTTPRequestHandler):
    # set by the server factory below
    db = DEFAULT_DB
    static_dir = DEFAULT_STATIC
    loop_cmd = "hearth-loop"
    ollama_url = OLLAMA_URL

    def log_message(self, *args):
        pass  # quiet; journald already captures the unit's output

    def _send(self, code, body, ctype="text/plain; charset=utf-8"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?", 1)[0] != "/healthz" and not request_allowed(
                self.client_address[0], self.headers.get("Authorization"), API_TOKEN):
            return self._send(403, "forbidden")
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/healthz":
            return self._send(200, "ok")
        if path == "/state":
            return self._send(
                200, json.dumps({"agents": read_snapshot(self.db)}),
                "application/json",
            )
        if path == "/events":
            return self._serve_events()
        if path == "/stats":
            return self._send(200, json.dumps(read_stats()), "application/json")
        if path == "/models":
            return self._send(200, json.dumps({"models": read_models()}), "application/json")
        if path in ("/v1/models", "/v1/models/"):
            return self._send(200, json.dumps(openai_models(read_models(), int(time.time()))),
                              "application/json")
        if path == "/runs":
            return self._send(200, json.dumps({"runs": read_runs(self.db)}), "application/json")
        if path == "/command":
            return self._serve_static("command.html", "text/html; charset=utf-8")
        if path == "/world":
            return self._serve_static("world.html", "text/html; charset=utf-8")
        if path == "/pending":
            return self._send(200, json.dumps({"pending": read_pending(self.db)}), "application/json")
        if path == "/transcript":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            agent = (qs.get("agent") or [""])[0]
            return self._send(200, json.dumps({"transcript": read_transcript(self.db, agent)}),
                              "application/json")
        if path == "/tree":
            return self._send(200, json.dumps({"nodes": read_tree(self.db)}), "application/json")
        if path == "/growth":
            return self._send(200, json.dumps(read_growth(self.db)), "application/json")
        if path == "/promote/diff":
            return self._send(200, json.dumps({"diff": promote_diff()}), "application/json")
        if path == "/promote/status":
            return self._send(200, json.dumps(promote_status()), "application/json")
        if path == "/promote/history":
            return self._send(200, json.dumps({"history": read_promote_history()}), "application/json")
        if path == "/schedule":
            return self._send(200, json.dumps({"missions": read_schedule()}), "application/json")
        if path == "/stats/history":
            return self._send(200, json.dumps(read_stats_history(self.db)), "application/json")
        if path == "/budget":
            return self._send(200, json.dumps(read_budget(self.db)), "application/json")
        if path == "/tools":
            return self._send(200, json.dumps({"tools": read_tools()}), "application/json")
        if path == "/router":
            return self._send(200, json.dumps(read_router()), "application/json")
        if path == "/egress":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            agent = (qs.get("agent") or [""])[0]
            limit = (qs.get("limit") or ["200"])[0]
            blocked = (qs.get("blocked") or ["0"])[0] == "1"
            return self._send(200, json.dumps(
                {"egress": read_egress(self.db, agent, limit=limit, blocked=blocked)}),
                "application/json")
        if path == "/security":
            return self._send(200, json.dumps(read_security(self.db)), "application/json")
        if path == "/tripwires":
            return self._send(200, json.dumps({"tripwires": read_tripwires(self.db)}),
                              "application/json")
        if path == "/replay":
            return self._serve_static("replay.html", "text/html; charset=utf-8")
        if path == "/replay/agents":
            return self._send(200, json.dumps({"agents": read_replay_agents(self.db)}),
                              "application/json")
        if path == "/replay/data":
            qs = urllib.parse.parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
            agent = (qs.get("agent") or [""])[0]
            if not agent:
                return self._send(400, json.dumps({"error": "agent required"}),
                                  "application/json")
            return self._send(200, json.dumps({"agent": agent,
                                               "steps": read_replay_steps(self.db, agent)}),
                              "application/json")
        if path == "/metrics":
            return self._send(200, prometheus_metrics(self.db), "text/plain; version=0.0.4; charset=utf-8")
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "events":
            return self._serve_session_events(parts[1])
        return self._send(404, "not found")

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw.decode() or "{}")
        except ValueError:
            return {}

    def do_POST(self):
        if self.path.split("?", 1)[0] != "/healthz" and not request_allowed(
                self.client_address[0], self.headers.get("Authorization"), API_TOKEN):
            return self._send(403, "forbidden")
        # Rate-limit remote callers (local cockpit/tools are trusted, unlimited).
        ip = self.client_address[0]
        if ip not in LOCAL_IPS:
            with _RATE_LOCK:
                ok = rate_allow(ip, time.monotonic(), _RATE_STORE)
            if not ok:
                return self._send(429, json.dumps({"error": "rate limit exceeded"}), "application/json")
        path = self.path.split("?", 1)[0]
        if path == "/chat":
            return self._handle_chat()
        if path == "/run":
            return self._handle_run()
        if path == "/session":
            return self._handle_new_session()
        if path == "/stop-all":
            return self._handle_stop_all()
        if path == "/decide":
            req = self._read_json_body()
            ok = decide_action(self.db, req.get("id"), bool(req.get("allow")))
            return self._send(200, json.dumps({"ok": ok}), "application/json")
        if path == "/grow-daemon":
            req = self._read_json_body()
            ok, detail = grow_daemon_action(req.get("action") or "")
            return self._send(200, json.dumps({"ok": ok, "detail": detail}), "application/json")
        if path == "/promote":
            req = self._read_json_body()
            ok, detail = promote_run(req.get("mode") or "")
            return self._send(200, json.dumps({"ok": ok, "detail": detail}), "application/json")
        if path in ("/v1/chat/completions", "/chat/completions"):
            return self._handle_openai_chat()
        if path == "/diff":
            return self._handle_diff()
        if path == "/ask":
            return self._handle_ask()
        if path == "/schedule":
            mid, err = schedule_add(self._read_json_body())
            return self._send(200 if mid else 400, json.dumps({"id": mid, "error": err}), "application/json")
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "schedule" and parts[2] in ("delete", "toggle"):
            # nix-declared missions are read-only here: edit the config and
            # rebuild instead. Refuse before touching the registry.
            if parts[1].startswith("nix-"):
                return self._send(400, json.dumps({"ok": False, "error": "declared in nix"}),
                                  "application/json")
            ok = (schedule_remove(parts[1]) if parts[2] == "delete" else schedule_toggle(parts[1]))
            return self._send(200, json.dumps({"ok": ok}), "application/json")
        if len(parts) == 3 and parts[0] == "session" and parts[2] == "send":
            return self._handle_session_send(parts[1])
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

    def _handle_diff(self):
        """A/B one prompt across two local models and return both answers with
        timing, so the cockpit can compare models side by side."""
        req = self._read_json_body()
        prompt = req.get("prompt") or ""
        model_a = req.get("model_a") or ""
        model_b = req.get("model_b") or ""
        if not prompt or not model_a or not model_b:
            return self._send(400, json.dumps(
                {"error": "prompt, model_a and model_b required"}), "application/json")
        return self._send(200, json.dumps(run_diff(self.db, prompt, model_a, model_b)),
                          "application/json")

    def _handle_ask(self):
        """Plain-English question over the local audit log: hand it to
        hearth_askdb.ask, which asks a local model for one read-only SELECT,
        validates it, runs it against the audit db, and summarizes the rows. The
        model call is injected here (a wrapper over chat_once). May make two model
        calls, so it can be slow; that is acceptable. Returns ask()'s dict as-is,
        or 400 on a missing question."""
        req = self._read_json_body()
        question = (req.get("question") or "").strip()
        if not question:
            return self._send(400, json.dumps({"error": "question required"}), "application/json")
        _ensure_agent_path()
        try:
            import hearth_askdb as _askdb
        except Exception as exc:  # noqa: BLE001
            return self._send(500, json.dumps(
                {"ok": False, "error": "hearth_askdb unavailable: {}".format(exc), "sql": None}),
                "application/json")
        chat_fn = _ASK_CHAT_HOOK.get("fn")
        if chat_fn is None:
            model = os.environ.get("HEARTH_ASK_MODEL") or ""
            if not model:
                avail = read_models()
                model = avail[0] if avail else "llama3.2:3b"

            def chat_fn(messages):
                reply, _tin, _tout = chat_once(OLLAMA_URL, model, messages)
                return reply
        result = _askdb.ask(question, db=self.db, chat_fn=chat_fn)
        return self._send(200, json.dumps(result), "application/json")

    def _handle_openai_chat(self):
        """OpenAI-compatible /v1/chat/completions: any OpenAI client can point at
        hearth and get a local model, with the call recorded to the audit log.
        Supports stream:true (SSE chunks). Auth reuses the bearer token."""
        req = self._read_json_body()
        messages = req.get("messages") or []
        model = req.get("model") or ""
        # Map an unknown/placeholder model (clients often send "gpt-4o" etc.) to a
        # real local model so generic OpenAI configs just work.
        avail = read_models()
        if model not in avail:
            model = avail[0] if avail else "llama3.2:3b"
        stream = bool(req.get("stream"))
        created = int(time.time())
        cid = "chatcmpl-" + uuid.uuid4().hex[:24]
        t0 = time.monotonic()

        if not stream:
            error, reply, tin, tout = None, "", 0, 0
            try:
                reply, tin, tout = chat_once(OLLAMA_URL, model, messages)
            except (urllib.error.URLError, OSError, ValueError) as exc:
                error = "{}: {}".format(type(exc).__name__, exc)
            _record_chat_run(self.db, "openai-api", model, tin, tout,
                             int((time.monotonic() - t0) * 1000), error)
            if error:
                return self._send(502, json.dumps({"error": {"message": error, "type": "upstream_error"}}),
                                  "application/json")
            return self._send(200, json.dumps(openai_completion(model, reply, tin, tout, created, cid)),
                              "application/json")

        # Streaming: forward Ollama tokens as OpenAI chunks as they arrive.
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def _chunk(delta, finish=None):
            self.wfile.write(("data: " + json.dumps(openai_chunk(model, created, cid, delta, finish)) + "\n\n").encode())
            self.wfile.flush()
        full, tin, tout, error = "", 0, 0, None
        try:
            _chunk({"role": "assistant"})
            for delta, done, p, e in chat_stream(OLLAMA_URL, model, messages):
                if delta:
                    full += delta
                    _chunk({"content": delta})
                if done:
                    tin, tout = p, e
            _chunk({}, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = "{}: {}".format(type(exc).__name__, exc)
        _record_chat_run(self.db, "openai-api", model, tin, tout,
                         int((time.monotonic() - t0) * 1000), error)

    def _handle_run(self):
        req = self._read_json_body()
        name = (req.get("name") or "agent").replace("/", "_").replace(" ", "_")[:40] or "agent"
        # "auto" (or an empty model) is a valid choice: the agent loop resolves it
        # via the router at run time, so pass it through unchanged and never force
        # it to a concrete model here.
        model = req.get("model") or "auto"
        prompt = req.get("prompt") or ""
        mode = req.get("mode") or "bypass"
        if mode not in ("plan", "auto", "bypass"):
            mode = "bypass"
        creds = req.get("creds")
        allowed = ",".join(creds) if isinstance(creds, list) else (creds or "")
        tools = req.get("tools")
        tools = ",".join(tools) if isinstance(tools, list) else (tools or "")
        hosts = req.get("allowed_hosts")
        hosts = ",".join(hosts) if isinstance(hosts, list) else (hosts or "")
        swarm = bool(req.get("swarm"))
        marathon = bool(req.get("marathon"))
        checkin = bool(req.get("checkin"))
        evolve = bool(req.get("evolve"))
        grow = bool(req.get("grow"))
        # The growth loop generates its own improvement goals, so it needs no prompt.
        if not prompt and not grow:
            return self._send(400, json.dumps({"error": "prompt required"}), "application/json")
        run_id = "{}-{}".format(name, uuid.uuid4().hex[:8])
        queue_dir = "/var/lib/hearth/queue"
        try:
            os.makedirs(queue_dir, exist_ok=True)
            tmp = os.path.join(queue_dir, run_id + ".json.tmp")
            final = os.path.join(queue_dir, run_id + ".json")
            with open(tmp, "w") as fh:
                json.dump({"name": name, "model": model, "prompt": prompt,
                           "mode": mode, "creds": allowed, "tools": tools,
                           "allowed_hosts": hosts, "swarm": swarm,
                           "marathon": marathon, "checkin": checkin,
                           "evolve": evolve, "grow": grow}, fh)
            os.replace(tmp, final)
        except OSError as exc:
            return self._send(500, json.dumps({"error": str(exc)}), "application/json")
        kick_spawn()
        self._send(200, json.dumps({"queued": run_id}), "application/json")

    def _handle_new_session(self):
        req = self._read_json_body()
        name = (req.get("name") or "session").replace("/", "_").replace(" ", "_")[:40] or "session"
        model = req.get("model") or "llama3.2:3b"
        mode = req.get("mode") or "auto"
        if mode not in ("plan", "auto", "bypass"):
            mode = "auto"
        task = req.get("task") or ""
        creds = req.get("creds")
        allowed = ",".join(creds) if isinstance(creds, list) else (creds or "")
        tools = req.get("tools")
        tools = ",".join(tools) if isinstance(tools, list) else (tools or "")
        hosts = req.get("allowed_hosts")
        hosts = ",".join(hosts) if isinstance(hosts, list) else (hosts or "")
        sid = "{}-{}".format(name, uuid.uuid4().hex[:8])
        workspace = "/var/lib/hearth/agents/" + sid
        with SESSIONS_LOCK:
            # Lazy reap: drop finished sessions so the registry cannot grow without
            # bound when clients never stream or disconnect early.
            for dead in [k for k, s in SESSIONS.items() if s.closed]:
                SESSIONS.pop(dead, None)
            full = len(SESSIONS) >= MAX_SESSIONS
        if full:
            return self._send(503, json.dumps({"error": "too many active sessions"}),
                              "application/json")
        try:
            sess = spawn_session(self.loop_cmd, sid, model, mode, workspace,
                                 self.db, self.ollama_url, allowed_creds=allowed,
                                 allowed_tools=tools, allowed_hosts=hosts)
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
        units = 0
        try:
            out = subprocess.run(
                [SYSTEMCTL, "list-units", "--plain", "--no-legend", "hearth-agent@*.service"],
                capture_output=True, text=True, timeout=5).stdout
            names = [ln.split()[0] for ln in out.splitlines() if ln.strip()]
            for name in names:
                r = subprocess.run([SUDO, "-n", SYSTEMCTL, "stop", name],
                                   capture_output=True, text=True, timeout=10)
                if r.returncode == 0:
                    units += 1
        except (OSError, subprocess.SubprocessError):
            pass
        cleared = deny_all_pending(self.db)
        return self._send(200, json.dumps({"stopped_sessions": len(sessions),
                                            "stopped_units": units,
                                            "cleared_pending": cleared}), "application/json")

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
                if closed and not evs:
                    with SESSIONS_LOCK:
                        SESSIONS.pop(sid, None)
                    return
                time.sleep(0.2)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def _serve_static(self, name, ctype):
        fpath = os.path.join(self.static_dir, name)
        try:
            with open(fpath, "rb") as fh:
                body = fh.read()
        except OSError:
            return self._send(404, "missing " + name)
        return self._send(200, body, ctype)

    def _serve_events(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_id = 0
        # Send the current snapshot first so a fresh client is immediately correct.
        try:
            snap = {"type": "snapshot", "agents": read_snapshot(self.db)}
            self.wfile.write(("data: " + json.dumps(snap) + "\n\n").encode())
            self.wfile.flush()
            last_id = max_event_id(self.db)
            heartbeat = 0
            while True:
                events = read_events_since(self.db, last_id)
                for ev in events:
                    last_id = ev["id"]
                    msg = {"type": "event", **ev}
                    self.wfile.write(("data: " + json.dumps(msg) + "\n\n").encode())
                self.wfile.flush()
                heartbeat += 1
                if heartbeat % 40 == 0:  # ~ every 10s, keep the connection warm
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                time.sleep(0.25)
        except (BrokenPipeError, ConnectionResetError, OSError):
            return  # client went away


def _self_test():
    g = parse_gpu("NVIDIA GeForce RTX 2060, 13, 2538, 6144\n")
    assert g == {"name": "NVIDIA GeForce RTX 2060", "util_pct": 13,
                 "mem_used_mb": 2538, "mem_total_mb": 6144}, g
    m = parse_meminfo("MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n")
    assert m == {"used_mb": 8000, "total_mb": 16000}, m
    assert parse_models('{"models":[{"name":"llama3.2:3b"},{"name":"mistral:7b"}]}') == ["llama3.2:3b", "mistral:7b"], "parse_models"
    assert parse_models("not json") == [], "parse_models bad"
    assert request_allowed("127.0.0.1", None, "") is True, "localhost open"
    assert request_allowed("192.168.1.9", None, "secret") is False, "remote no token"
    assert request_allowed("192.168.1.9", "Bearer secret", "secret") is True, "remote good token"
    assert request_allowed("192.168.1.9", "Bearer wrong", "secret") is False, "remote bad token"
    assert request_allowed("192.168.1.9", None, "") is False, "no token configured -> remote denied"
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
    import tempfile as _tft
    tdb = os.path.join(_tft.mkdtemp(prefix="hearth-tree-"), "t.db")
    con = sqlite3.connect(tdb)
    con.executescript(SCHEMA)
    con.execute("CREATE TABLE IF NOT EXISTS agent_meta (agent_id TEXT PRIMARY KEY, parent_id TEXT, kind TEXT, goal TEXT, created_at TEXT)")
    con.execute("INSERT INTO agent_meta VALUES (?,?,?,?,?)", ("mgr", None, "manager", "do it", _now_iso()))
    con.execute("INSERT INTO agent_meta VALUES (?,?,?,?,?)", ("mgr-s1", "mgr", "specialist", "part one", _now_iso()))
    con.execute("INSERT INTO agent_state (agent_id, state, detail, updated_at) VALUES (?,?,?,?)", ("mgr", "WAITING_IO", "2 running", _now_iso()))
    con.commit(); con.close()
    nodes = {n["agent_id"]: n for n in read_tree(tdb)}
    assert nodes["mgr"]["kind"] == "manager" and nodes["mgr"]["state"] == "WAITING_IO", nodes
    assert nodes["mgr-s1"]["parent_id"] == "mgr" and nodes["mgr-s1"]["state"] is None, nodes
    # OpenAI-compatible response shaping
    comp = openai_completion("llama3.2:3b", "hi there", 5, 2, 1000, "chatcmpl-x")
    assert comp["object"] == "chat.completion" and comp["choices"][0]["message"]["content"] == "hi there", comp
    assert comp["usage"]["total_tokens"] == 7 and comp["choices"][0]["finish_reason"] == "stop", comp
    ch = openai_chunk("m", 1, "id", {"content": "x"})
    assert ch["object"] == "chat.completion.chunk" and ch["choices"][0]["delta"] == {"content": "x"}, ch
    assert ch["choices"][0]["finish_reason"] is None
    ml = openai_models(["a", "b"], 1)
    assert ml["object"] == "list" and [d["id"] for d in ml["data"]] == ["a", "b"], ml
    assert all(d["object"] == "model" for d in ml["data"]), ml

    # schedule registry helpers (add / read / toggle / remove) on a temp path
    import tempfile
    sreg = os.path.join(tempfile.mkdtemp(prefix="hearth-sched-"), "s.json")
    assert read_schedule(sreg) == []
    mid, err = schedule_add({"name": "digest", "goal": "summarize the day",
                             "every_minutes": 1440, "kind": "marathon"}, path=sreg)
    assert mid and not err, (mid, err)
    got = read_schedule(sreg)
    assert len(got) == 1 and got[0]["schedule"] == {"every_minutes": 1440}, got
    _, err2 = schedule_add({"name": "bad"}, path=sreg)  # no goal/schedule
    assert err2, "missing goal/schedule rejected"
    assert schedule_toggle(mid, path=sreg) and read_schedule(sreg)[0]["enabled"] is False
    assert schedule_remove(mid, path=sreg) and read_schedule(sreg) == []

    # v1.5 declarative missions: read_schedule appends the nix-declared file
    # (env HEARTH_MISSIONS) after the registry, with sources marked, and the
    # write paths never leak nix entries or source marks into the registry file.
    sreg2 = os.path.join(tempfile.mkdtemp(prefix="hearth-sched2-"), "s.json")
    decl = os.path.join(tempfile.mkdtemp(prefix="hearth-decl-"), "missions.json")
    with open(decl, "w") as fh:
        json.dump([{"name": "nightly-digest", "kind": "agent", "model": "m",
                    "prompt": "summarize the day", "schedule": {"at": "07:00"},
                    "enabled": False},
                   {"kind": "agent"}], fh)  # nameless entry is skipped
    mid2, err = schedule_add({"name": "usr", "goal": "watch the logs",
                              "every_minutes": 5}, path=sreg2)
    assert mid2 and not err, (mid2, err)
    prev_missions = os.environ.pop("HEARTH_MISSIONS", None)
    try:
        os.environ["HEARTH_MISSIONS"] = decl
        merged = read_schedule(sreg2)
        assert [m["source"] for m in merged] == ["user", "nix"], merged
        assert merged[0]["id"] == mid2, merged
        assert merged[1]["id"] == "nix-nightly-digest" and merged[1]["enabled"] is False, merged
        assert merged[1]["kind"] == "agent" and merged[1]["schedule"] == {"at": "07:00"}, merged
        # toggling the user mission must not persist source marks or nix rows
        assert schedule_toggle(mid2, path=sreg2)
        with open(sreg2) as fh:
            raw = json.load(fh)
        assert len(raw) == 1 and raw[0]["id"] == mid2 and "source" not in raw[0], raw
        # missing or invalid declarative file = registry only, silently
        os.environ["HEARTH_MISSIONS"] = decl + ".nope"
        assert [m["id"] for m in read_schedule(sreg2)] == [mid2]
        bad = decl + ".bad"
        with open(bad, "w") as fh:
            fh.write("not json")
        os.environ["HEARTH_MISSIONS"] = bad
        assert [m["id"] for m in read_schedule(sreg2)] == [mid2], "invalid file skipped"
        os.environ["HEARTH_MISSIONS"] = decl
        # HTTP layer: /schedule/nix-*/toggle and /delete are refused with 400
        # before the registry is touched.
        srv_n = make_server("127.0.0.1", 0, sreg2 + ".db", DEFAULT_STATIC)
        threading.Thread(target=srv_n.serve_forever, daemon=True).start()
        base_n = "http://127.0.0.1:{}".format(srv_n.server_address[1])
        try:
            for act in ("toggle", "delete"):
                try:
                    urllib.request.urlopen(urllib.request.Request(
                        base_n + "/schedule/nix-nightly-digest/" + act, data=b"{}",
                        headers={"Content-Type": "application/json"}), timeout=5)
                    raise AssertionError("nix mission " + act + " must 400")
                except urllib.error.HTTPError as exc:
                    assert exc.code == 400, exc.code
                    got = json.loads(exc.read().decode())
                    assert got == {"ok": False, "error": "declared in nix"}, got
            with urllib.request.urlopen(base_n + "/schedule", timeout=5) as resp:
                got = json.loads(resp.read().decode())
            nix_rows = [m for m in got["missions"] if m.get("source") == "nix"]
            assert [m["id"] for m in nix_rows] == ["nix-nightly-digest"], got
        finally:
            srv_n.shutdown()
            srv_n.server_close()
    finally:
        if prev_missions is None:
            os.environ.pop("HEARTH_MISSIONS", None)
        else:
            os.environ["HEARTH_MISSIONS"] = prev_missions

    # Prometheus metrics: counters + per-model + daemon gauges (daemon injected).
    mtxt = prometheus_metrics(tdb, units=("hearth-grow.service",), is_active_fn=lambda u: True)
    assert "hearth_runs_total" in mtxt and "# TYPE hearth_runs_total counter" in mtxt, mtxt
    assert 'hearth_daemon_up{unit="hearth-grow.service"} 1' in mtxt, mtxt
    assert "hearth_errors_total" in mtxt and "hearth_tokens_total" in mtxt, mtxt

    # rate limiter: allows up to the limit in a window, then blocks; the window slides.
    store = {}
    assert all(rate_allow("1.2.3.4", 100.0 + i, store, limit=3, window=60) for i in range(3))
    assert rate_allow("1.2.3.4", 100.1, store, limit=3, window=60) is False, "4th in window blocked"
    assert rate_allow("9.9.9.9", 100.1, store, limit=3, window=60) is True, "other ip independent"
    assert rate_allow("1.2.3.4", 200.0, store, limit=3, window=60) is True, "old hits expired -> allowed"

    # cloud-cost-saved: the counter derives from total tokens at the blended rate,
    # and rides along in /stats/history day rows and totals.
    assert cloud_saved_usd(1_000_000) == round(CLOUD_PRICE_PER_MTOK, 2)
    assert cloud_saved_usd(0) == 0.0 and cloud_saved_usd(None) == 0.0
    sdb = os.path.join(tempfile.mkdtemp(prefix="hearth-saved-"), "s.db")
    con = sqlite3.connect(sdb)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
                "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
                "VALUES ('a','r1','2026-07-15T00:00:00','2026-07-15T00:00:01',0,2000000,0,5,NULL,'m')")
    con.commit(); con.close()
    hist = read_stats_history(sdb)
    assert hist["totals"]["saved_usd"] == cloud_saved_usd(2_000_000), hist["totals"]
    assert hist["by_day"][0]["saved_usd"] == cloud_saved_usd(2_000_000), hist["by_day"]

    # egress log reader + the security scoreboard aggregation
    con = sqlite3.connect(sdb)
    con.execute("CREATE TABLE IF NOT EXISTS egress_log (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "agent_id TEXT, ts TEXT, tool TEXT, host TEXT, url TEXT, allowed INTEGER)")
    con.execute("INSERT INTO egress_log (agent_id, ts, tool, host, url, allowed) "
                "VALUES ('w1', ?, 'web_fetch', 'evil.com', 'https://evil.com/x', 0)", (_now_iso(),))
    con.execute("INSERT INTO egress_log (agent_id, ts, tool, host, url, allowed) "
                "VALUES ('w1', ?, 'http_request', 'api.github.com', 'https://api.github.com', 1)", (_now_iso(),))
    con.commit(); con.close()
    eg = read_egress(sdb)
    assert len(eg) == 2 and eg[0]["host"] == "api.github.com", eg  # newest first
    assert read_egress(sdb, agent="nobody") == []
    blocked = [e for e in eg if not e["allowed"]]
    assert blocked and blocked[0]["host"] == "evil.com", eg
    # v1.4: rows carry ids (so the world HUD can track the newest blocked hit),
    # blocked=True filters to allowed=0 only, and limit is coerced + capped.
    assert eg[0]["id"] > eg[1]["id"], eg
    egb = read_egress(sdb, blocked=True, limit=5)
    assert len(egb) == 1 and egb[0]["host"] == "evil.com" and egb[0]["allowed"] is False, egb
    assert len(read_egress(sdb, limit=1)) == 1
    assert read_egress(sdb, limit="junk") == eg, "bad limit falls back to the default"
    assert read_egress(sdb, limit=9999) == eg, "limit is capped, not an error"
    sec = read_security(sdb, is_active_fn=lambda u: u == "hearth-mapd.service")
    assert sec["egress"] == {"logged": 2, "blocked": 1}, sec
    assert sec["tripwires"] == {"armed": False, "trips": 0}, sec  # not armed until a tripwires table exists
    # once a tripwire fires, the scoreboard shows armed + a count, and /tripwires reads it back
    con = sqlite3.connect(sdb)
    con.execute("CREATE TABLE IF NOT EXISTS tripwires (id INTEGER PRIMARY KEY AUTOINCREMENT, "
                "agent_id TEXT NOT NULL, ts TEXT NOT NULL, tool TEXT, path TEXT, token TEXT, detail TEXT)")
    con.execute("INSERT INTO tripwires (agent_id, ts, tool, path, token, detail) "
                "VALUES ('w9', ?, 'read_file', '.aws/credentials', 'HEARTH-CANARY-abc', 'read the decoy')", (_now_iso(),))
    con.commit(); con.close()
    tw = read_tripwires(sdb)
    assert len(tw) == 1 and tw[0]["agent"] == "w9" and tw[0]["tool"] == "read_file", tw
    sec2 = read_security(sdb, is_active_fn=lambda u: False)
    assert sec2["tripwires"] == {"armed": True, "trips": 1}, sec2
    assert sec["daemons"]["hearth-mapd.service"] is True and sec["daemons"]["hearth-grow.service"] is False, sec
    assert sec["manifests_supported"] is True

    # v1.4: the egress_os flag mirrors the env the NixOS module sets on the
    # mapd service when OS-level (nftables) enforcement is switched on.
    prev_os = os.environ.pop("HEARTH_EGRESS_OS", None)
    try:
        assert read_security(sdb, is_active_fn=lambda u: False)["egress_os"] is False
        os.environ["HEARTH_EGRESS_OS"] = "1"
        assert read_security(sdb, is_active_fn=lambda u: False)["egress_os"] is True
        os.environ["HEARTH_EGRESS_OS"] = "0"
        assert read_security(sdb, is_active_fn=lambda u: False)["egress_os"] is False
    finally:
        if prev_os is None:
            os.environ.pop("HEARTH_EGRESS_OS", None)
        else:
            os.environ["HEARTH_EGRESS_OS"] = prev_os

    # v1.5 budget breaker: today's tokens (UTC day) vs HEARTH_DAILY_TOKEN_CAP,
    # through the reader, through GET /budget, and onto the security scoreboard.
    bdb = os.path.join(tempfile.mkdtemp(prefix="hearth-budget-"), "b.db")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    yday = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%d")
    con = sqlite3.connect(bdb)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
        "tokens_in, tokens_out, cost_usd, latency_ms, error, model) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("a", "r-t1", today + "T01:00:00", today + "T01:00:01", 100, 300, 0, 5, None, "m"),
         ("a", "r-t2", today + "T02:00:00", today + "T02:00:01", 200, 400, 0, 5, None, "m"),
         ("a", "r-y1", yday + "T01:00:00", yday + "T01:00:01", 5000, 5000, 0, 5, None, "m")])
    con.commit(); con.close()
    prev_cap = os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
    prev_tg = os.environ.pop("TELEGRAM_BOT_TOKEN", None)
    prev_nt = os.environ.pop("HEARTH_NTFY_TOPIC", None)
    try:
        b = read_budget(bdb)  # cap unset: yesterday's tokens never count
        assert b == {"cap": 0, "tokens_today": 1000, "runs_today": 2,
                     "remaining": 0, "capped": False}, b
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "5000"
        b = read_budget(bdb)  # under the cap: breaker armed, not open
        assert b["cap"] == 5000 and b["tokens_today"] == 1000, b
        assert b["remaining"] == 4000 and b["capped"] is False, b
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "800"
        b = read_budget(bdb)  # over the cap: breaker open
        assert b["capped"] is True and b["remaining"] == 0, b
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "junk"
        assert read_budget(bdb)["cap"] == 0, "unparseable cap falls back to no cap"
        assert read_budget(os.path.join(tempfile.mkdtemp(prefix="hearth-nob-"), "x.db")) == {
            "cap": 0, "tokens_today": 0, "runs_today": 0, "remaining": 0, "capped": False}
        # the same shape through the HTTP layer
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "800"
        srv_bg = make_server("127.0.0.1", 0, bdb, DEFAULT_STATIC)
        threading.Thread(target=srv_bg.serve_forever, daemon=True).start()
        try:
            u = "http://127.0.0.1:{}/budget".format(srv_bg.server_address[1])
            with urllib.request.urlopen(u, timeout=5) as resp:
                got = json.loads(resp.read().decode())
            assert got == {"cap": 800, "tokens_today": 1000, "runs_today": 2,
                           "remaining": 0, "capped": True}, got
        finally:
            srv_bg.shutdown()
            srv_bg.server_close()
        # security scoreboard: budget + alerting keys flip with the env
        sec_b = read_security(bdb, is_active_fn=lambda u: False)
        assert sec_b["budget"] == {"cap": 800, "capped": True}, sec_b
        assert sec_b["alerting"] == {"telegram_env": False, "ntfy": False}, sec_b
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "5000"
        os.environ["TELEGRAM_BOT_TOKEN"] = "tok"
        os.environ["HEARTH_NTFY_TOPIC"] = "hearth-alerts"
        sec_b = read_security(bdb, is_active_fn=lambda u: False)
        assert sec_b["budget"] == {"cap": 5000, "capped": False}, sec_b
        assert sec_b["alerting"] == {"telegram_env": True, "ntfy": True}, sec_b
    finally:
        for k, v in (("HEARTH_DAILY_TOKEN_CAP", prev_cap),
                     ("TELEGRAM_BOT_TOKEN", prev_tg), ("HEARTH_NTFY_TOPIC", prev_nt)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    # and the same filter through the HTTP layer: /egress?blocked=1&limit=5
    # returns only blocked rows (this is what the world HUD polls).
    srv_eg = make_server("127.0.0.1", 0, sdb, DEFAULT_STATIC)
    threading.Thread(target=srv_eg.serve_forever, daemon=True).start()
    try:
        u = "http://127.0.0.1:{}/egress?blocked=1&limit=5".format(srv_eg.server_address[1])
        with urllib.request.urlopen(u, timeout=5) as resp:
            got = json.loads(resp.read().decode())
        assert [e["host"] for e in got["egress"]] == ["evil.com"], got
        assert all(e["allowed"] is False and "id" in e for e in got["egress"]), got
    finally:
        srv_eg.shutdown()
        srv_eg.server_close()

    # /tools registry: importable in the repo layout; every entry carries a risk class
    tools_list = read_tools()
    assert tools_list, "agent registry should import in the repo layout"
    byname = {t["name"]: t for t in tools_list}
    assert byname["run_command"]["risk"] == "dangerous", byname["run_command"]
    assert byname["read_file"]["risk"] == "safe", byname["read_file"]

    # per-run scoping lands in the session child's environment
    senv = _session_env(allowed_creds="alpha", allowed_tools="read_file",
                        allowed_hosts="github.com")
    assert senv["HEARTH_ALLOWED_CREDS"] == "alpha"
    assert senv["HEARTH_ALLOWED_TOOLS"] == "read_file"
    assert senv["HEARTH_ALLOWED_HOSTS"] == "github.com"
    senv2 = _session_env()
    for k in ("HEARTH_ALLOWED_CREDS", "HEARTH_ALLOWED_TOOLS", "HEARTH_ALLOWED_HOSTS"):
        assert senv2.get(k) == os.environ.get(k), "empty scoping must not add " + k

    # --- replay: seed run_steps for two agents and read the log back, both
    # through the readers and through the live HTTP endpoints.
    rdb = os.path.join(tempfile.mkdtemp(prefix="hearth-replay-"), "r.db")
    con = sqlite3.connect(rdb)
    con.execute(REPLAY_SCHEMA)
    con.executemany(
        "INSERT INTO run_steps (agent_id, ts, seq, kind, tool, args, output, duration_ms, verdict) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        [("agent-old", "2026-07-18T00:00:00", 1, "think", "", "", "planning", 5, ""),
         ("agent-old", "2026-07-18T00:00:01", 2, "tool", "read_file", "{}", "ok", 12, "allow"),
         ("agent-old", "2026-07-18T00:00:02", 3, "done", "", "", "finished", 1, ""),
         ("agent-new", "2026-07-19T00:00:00", 1, "think", "", "", "hmm", 4, ""),
         ("agent-new", "2026-07-19T00:00:01", 2, "tool", "run_command", '{"cmd":"ls"}', "listing", 30, "gate:allow"),
         ("agent-new", "2026-07-19T00:00:02", 3, "error", "", "", "boom", 2, "")])
    con.commit(); con.close()
    ra = read_replay_agents(rdb)
    assert [a["agent_id"] for a in ra] == ["agent-new", "agent-old"], ra  # last_ts desc
    assert ra[0]["steps"] == 3 and ra[0]["tools"] == 1 and ra[0]["last_kind"] == "error", ra
    assert ra[1]["last_kind"] == "done" and ra[1]["first_ts"] == "2026-07-18T00:00:00", ra
    assert ra[1]["last_ts"] == "2026-07-18T00:00:02", ra
    rs = read_replay_steps(rdb, "agent-old")
    assert [s["seq"] for s in rs] == [1, 2, 3], rs  # seq asc
    assert rs[1]["tool"] == "read_file" and rs[1]["verdict"] == "allow", rs
    assert set(rs[0]) == {"seq", "ts", "kind", "tool", "args", "output", "duration_ms", "verdict"}, rs
    assert read_replay_steps(rdb, "nobody") == []
    assert read_replay_agents(os.path.join(tempfile.mkdtemp(prefix="hearth-nodb-"), "x.db")) == []

    # /diff engine with an injected chat fn (no Ollama needed): both sides run,
    # a failed side carries an error, and both turns land in the audit log.
    def _fake_chat(url, model, messages):
        if model == "bad":
            raise OSError("connect refused")
        return "reply from " + model, 3, 4
    d = run_diff(rdb, "compare this", "m-a", "m-b", chat_fn=_fake_chat)
    assert d["prompt"] == "compare this", d
    assert d["a"]["model"] == "m-a" and d["a"]["output"] == "reply from m-a", d
    assert d["a"]["tokens"] == 7 and "latency_ms" in d["a"], d
    assert d["b"]["output"] == "reply from m-b", d
    d2 = run_diff(rdb, "x", "m-a", "bad", chat_fn=_fake_chat)
    assert "OSError" in d2["b"].get("error", "") and "output" not in d2["b"], d2
    assert d2["a"]["output"] == "reply from m-a", d2
    diff_runs = [r for r in read_runs(rdb) if r["agent_name"] == "diff"]
    assert len(diff_runs) == 4, diff_runs

    # the same data through the HTTP layer, on an ephemeral local server
    srv = make_server("127.0.0.1", 0, rdb, DEFAULT_STATIC)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:{}".format(srv.server_address[1])
    try:
        with urllib.request.urlopen(base + "/replay/agents", timeout=5) as resp:
            got = json.loads(resp.read().decode())
        assert [a["agent_id"] for a in got["agents"]] == ["agent-new", "agent-old"], got
        with urllib.request.urlopen(base + "/replay/data?agent=agent-new", timeout=5) as resp:
            got = json.loads(resp.read().decode())
        assert got["agent"] == "agent-new" and [s["seq"] for s in got["steps"]] == [1, 2, 3], got
        try:
            urllib.request.urlopen(base + "/replay/data", timeout=5)
            raise AssertionError("empty agent param must 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400, exc.code
        # the replay page itself, when the static file has been built
        if os.path.exists(os.path.join(DEFAULT_STATIC, "replay.html")):
            with urllib.request.urlopen(base + "/replay", timeout=5) as resp:
                assert (resp.headers.get("Content-Type") or "").startswith("text/html"), resp.headers
                assert resp.read(), "replay page should not be empty"
        # /diff input validation: missing models -> 400
        try:
            urllib.request.urlopen(urllib.request.Request(
                base + "/diff", data=json.dumps({"prompt": "hi"}).encode(),
                headers={"Content-Type": "application/json"}), timeout=5)
            raise AssertionError("diff without models must 400")
        except urllib.error.HTTPError as exc:
            assert exc.code == 400, exc.code
    finally:
        srv.shutdown()
        srv.server_close()

    # v1.6 "Router": GET /router returns the active ruleset (seeded via a temp
    # router.json through HEARTH_ROUTER) plus the resolved path; POST /ask routes
    # to hearth_askdb (400 on a missing question, ok True with sql+summary on the
    # happy path, driven by an injected chat_fn so no local model is needed).
    prev_router = os.environ.pop("HEARTH_ROUTER", None)
    rdir = tempfile.mkdtemp(prefix="hearth-router-")
    rjson = os.path.join(rdir, "router.json")
    with open(rjson, "w") as fh:
        json.dump({"default": "llama3.2:3b",
                   "rules": [{"name": "code", "any_keywords": ["code", "bug"],
                              "tools_any": ["edit_file"], "model": "qwen2.5-coder:latest"},
                             {"name": "no-model", "any_keywords": ["skip"]}]}, fh)
    # A seeded audit db so /ask has real rows to select over.
    adb = os.path.join(rdir, "audit.db")
    con = sqlite3.connect(adb)
    con.executescript(SCHEMA)
    con.executemany(
        "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
        "tokens_in, tokens_out, cost_usd, latency_ms, error, model) VALUES (?,?,?,?,?,?,?,?,?,?)",
        [("demo", "r1", "2026-07-19T01:00:00", "2026-07-19T01:00:01", 100, 50, 0, 5, None, "llama3.2:3b"),
         ("demo", "r2", "2026-07-19T02:00:00", "2026-07-19T02:00:01", 200, 25, 0, 5, None, "qwen2.5-coder:latest")])
    con.commit(); con.close()
    try:
        os.environ["HEARTH_ROUTER"] = rjson
        # the reader itself: invalid rule (no model) is dropped, path is carried.
        rr = read_router()
        assert rr["path"] == rjson and rr["default"] == "llama3.2:3b", rr
        assert [r["name"] for r in rr["rules"]] == ["code"], rr
        srv_r = make_server("127.0.0.1", 0, adb, DEFAULT_STATIC)
        threading.Thread(target=srv_r.serve_forever, daemon=True).start()
        base_r = "http://127.0.0.1:{}".format(srv_r.server_address[1])
        try:
            with urllib.request.urlopen(base_r + "/router", timeout=5) as resp:
                got = json.loads(resp.read().decode())
            assert got["path"] == rjson and got["default"] == "llama3.2:3b", got
            assert [r["name"] for r in got["rules"]] == ["code"], got
            assert got["rules"][0]["model"] == "qwen2.5-coder:latest", got

            # POST /ask with no question -> 400
            try:
                urllib.request.urlopen(urllib.request.Request(
                    base_r + "/ask", data=json.dumps({"question": "   "}).encode(),
                    headers={"Content-Type": "application/json"}), timeout=5)
                raise AssertionError("empty question must 400")
            except urllib.error.HTTPError as exc:
                assert exc.code == 400, exc.code

            # POST /ask happy path via an injected chat_fn: first call returns the
            # SQL, second call returns the summary. hermetic (no Ollama).
            calls = {"n": 0}

            def _fake_ask_chat(messages):
                calls["n"] += 1
                if calls["n"] == 1:
                    return "SELECT agent_name, model FROM agent_runs ORDER BY id"
                return "Two runs are recorded, one per model."
            _ASK_CHAT_HOOK["fn"] = _fake_ask_chat
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        base_r + "/ask", data=json.dumps({"question": "which models ran?"}).encode(),
                        headers={"Content-Type": "application/json"}), timeout=30) as resp:
                    got = json.loads(resp.read().decode())
                assert got.get("ok") is True, got
                assert got.get("question") == "which models ran?", got
                assert got.get("sql") == "SELECT agent_name, model FROM agent_runs ORDER BY id", got
                assert got.get("summary") == "Two runs are recorded, one per model.", got
                assert [r[0] for r in got.get("rows") or []] == ["demo", "demo"], got
                assert got.get("columns") == ["agent_name", "model"], got
                assert calls["n"] == 2, calls  # one call for SQL, one for the summary
            finally:
                _ASK_CHAT_HOOK["fn"] = None

            # POST /ask rejects a non-SELECT the model might emit (ok False, db
            # untouched), proving the request really reaches hearth_askdb.
            _ASK_CHAT_HOOK["fn"] = lambda messages: "DROP TABLE agent_runs"
            try:
                with urllib.request.urlopen(urllib.request.Request(
                        base_r + "/ask", data=json.dumps({"question": "drop it"}).encode(),
                        headers={"Content-Type": "application/json"}), timeout=10) as resp:
                    got = json.loads(resp.read().decode())
                assert got.get("ok") is False and got.get("sql") == "DROP TABLE agent_runs", got
            finally:
                _ASK_CHAT_HOOK["fn"] = None
        finally:
            srv_r.shutdown()
            srv_r.server_close()
    finally:
        if prev_router is None:
            os.environ.pop("HEARTH_ROUTER", None)
        else:
            os.environ["HEARTH_ROUTER"] = prev_router

    print("hearth-mapd self-test OK")
    return 0


def make_server(host, port, db, static_dir, loop_cmd="hearth-loop"):
    Handler.db = db
    Handler.static_dir = static_dir
    Handler.loop_cmd = loop_cmd
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hearth-mapd")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--static-dir", default=DEFAULT_STATIC)
    parser.add_argument("--loop-cmd", default=os.environ.get("HEARTH_LOOP_CMD", "hearth-loop"),
                        help="command used to spawn an interactive agent loop")
    parser.add_argument("--self-test", action="store_true",
                        help="run the parser self-test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    server = make_server(args.host, args.port, args.db, args.static_dir, args.loop_cmd)
    print("hearth-mapd serving on http://{}:{} (db={})".format(args.host, args.port, args.db))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
