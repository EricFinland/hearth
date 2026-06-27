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
from datetime import datetime, timezone
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


def read_schedule(path=SCHEDULE_REGISTRY):
    try:
        with open(path) as fh:
            d = json.load(fh)
        return d if isinstance(d, list) else []
    except (OSError, ValueError):
        return []


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
    missions = read_schedule(path)
    missions.append(m)
    write_schedule(missions, path)
    return m["id"], ""


def schedule_remove(mid, path=SCHEDULE_REGISTRY):
    missions = read_schedule(path)
    kept = [m for m in missions if m.get("id") != mid]
    if len(kept) != len(missions):
        write_schedule(kept, path)
        return True
    return False


def schedule_toggle(mid, path=SCHEDULE_REGISTRY):
    missions = read_schedule(path)
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


def spawn_session(loop_cmd, sid, model, mode, workspace, db, ollama_url, allowed_creds=""):
    """Start a hearth-loop --session child and wrap it in a Session. The caller
    registers the returned Session in SESSIONS. allowed_creds (comma-separated)
    scopes which stored credentials the agent may read; empty means all."""
    os.makedirs(workspace, exist_ok=True)
    args = [loop_cmd, "--session", "--model", model, "--mode", mode,
            "--agent-name", sid, "--workspace", workspace, "--db", db,
            "--ollama-url", ollama_url]
    env = dict(os.environ)
    if allowed_creds:
        env["HEARTH_ALLOWED_CREDS"] = allowed_creds
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
        if path == "/schedule":
            mid, err = schedule_add(self._read_json_body())
            return self._send(200 if mid else 400, json.dumps({"id": mid, "error": err}), "application/json")
        parts = path.strip("/").split("/")
        if len(parts) == 3 and parts[0] == "schedule" and parts[2] in ("delete", "toggle"):
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
        error = None
        reply, tin, tout = "", 0, 0
        try:
            reply, tin, tout = chat_once(OLLAMA_URL, model, messages)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            error = "{}: {}".format(type(exc).__name__, exc)
        latency = int((time.monotonic() - t0) * 1000)
        _record_chat_run(self.db, "openai-api", model, tin, tout, latency, error)
        if error:
            return self._send(502, json.dumps({"error": {"message": error, "type": "upstream_error"}}),
                              "application/json")
        if not stream:
            return self._send(200, json.dumps(openai_completion(model, reply, tin, tout, created, cid)),
                              "application/json")
        # Streaming: role delta, content delta, stop, [DONE].
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()

        def _chunk(delta, finish=None):
            self.wfile.write(("data: " + json.dumps(openai_chunk(model, created, cid, delta, finish)) + "\n\n").encode())
            self.wfile.flush()
        try:
            _chunk({"role": "assistant"})
            _chunk({"content": reply})
            _chunk({}, finish="stop")
            self.wfile.write(b"data: [DONE]\n\n")
            self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass

    def _handle_run(self):
        req = self._read_json_body()
        name = (req.get("name") or "agent").replace("/", "_").replace(" ", "_")[:40] or "agent"
        model = req.get("model") or "llama3.2:3b"
        prompt = req.get("prompt") or ""
        mode = req.get("mode") or "bypass"
        if mode not in ("plan", "auto", "bypass"):
            mode = "bypass"
        creds = req.get("creds")
        allowed = ",".join(creds) if isinstance(creds, list) else (creds or "")
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
                           "mode": mode, "creds": allowed, "swarm": swarm,
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
                                 self.db, self.ollama_url, allowed_creds=allowed)
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
