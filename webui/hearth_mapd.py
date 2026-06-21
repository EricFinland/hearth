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
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
DEFAULT_STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
OLLAMA_URL = os.environ.get("HEARTH_OLLAMA", "http://127.0.0.1:11434")

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


class Handler(BaseHTTPRequestHandler):
    # set by the server factory below
    db = DEFAULT_DB
    static_dir = DEFAULT_STATIC

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
        if path == "/command":
            return self._serve_static("command.html", "text/html; charset=utf-8")
        return self._send(404, "not found")

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
    print("hearth-mapd self-test OK")
    return 0


def make_server(host, port, db, static_dir):
    Handler.db = db
    Handler.static_dir = static_dir
    return ThreadingHTTPServer((host, port), Handler)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="hearth-mapd")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--static-dir", default=DEFAULT_STATIC)
    parser.add_argument("--self-test", action="store_true",
                        help="run the parser self-test and exit")
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    server = make_server(args.host, args.port, args.db, args.static_dir)
    print("hearth-mapd serving on http://{}:{} (db={})".format(args.host, args.port, args.db))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
