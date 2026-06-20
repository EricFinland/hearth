#!/usr/bin/env python3
"""hearth-dashboard: a terminal dashboard showing hearth system state, model
status, recent agent runs, and spend. Built on Textual.

Run modes:
  hearth-dashboard              launch the full-screen TUI
  hearth-dashboard --plain      print a one-shot text snapshot and exit. This is
                                also the fallback when there is no TUI-capable
                                terminal (see modules/shell.nix login hook).
  hearth-dashboard --self-test  exercise the data layer against a temp database

Design notes:
  - The data-gathering functions are pure and defensive. Any failure (Ollama
    down, no systemctl, database missing) degrades to a safe placeholder rather
    than crashing the dashboard.
  - Textual is imported lazily inside make_app(), so --plain and --self-test run
    on a machine that does not have Textual installed (for example during local
    development on Windows).
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone

DB_PATH = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
OLLAMA_URL = os.environ.get("HEARTH_OLLAMA", "http://127.0.0.1:11434")

# (label shown, systemd unit queried)
UNITS = [
    ("ollama", "ollama.service"),
    ("audit", "hearth-audit-init.service"),
    ("tailscale", "tailscaled.service"),
]


def _systemctl(*args):
    return subprocess.run(
        ["systemctl", *args],
        capture_output=True,
        text=True,
        timeout=5,
    )


def unit_active(unit):
    try:
        result = _systemctl("is-active", unit)
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def system_status():
    return [(label, unit_active(unit)) for label, unit in UNITS]


def running_agents():
    """Count active hearth-* service units (the running agents)."""
    try:
        result = _systemctl(
            "list-units",
            "--type=service",
            "--state=running",
            "--no-legend",
            "--plain",
            "hearth-*",
        )
        return len([ln for ln in result.stdout.splitlines() if ln.strip()])
    except (OSError, subprocess.SubprocessError):
        return 0


def model_status():
    try:
        url = OLLAMA_URL.rstrip("/") + "/api/tags"
        with urllib.request.urlopen(url, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        return [m.get("name", "?") for m in data.get("models", [])]
    except (urllib.error.URLError, OSError, ValueError):
        return []


def _connect(db):
    if not os.path.exists(db):
        return None
    try:
        return sqlite3.connect(db)
    except sqlite3.Error:
        return None


def recent_runs(db, limit=20):
    con = _connect(db)
    if con is None:
        return []
    try:
        cur = con.execute(
            "SELECT started_at, agent_name, model, tokens_in, tokens_out, "
            "latency_ms, cost_usd, error FROM agent_runs "
            "ORDER BY started_at DESC LIMIT ?",
            (limit,),
        )
        return cur.fetchall()
    except sqlite3.Error:
        return []
    finally:
        con.close()


def spend_summary(db):
    con = _connect(db)
    if con is None:
        return {"today": 0.0, "month": 0.0, "runs": 0}
    try:
        now = datetime.now(timezone.utc)
        today = now.strftime("%Y-%m-%d")
        month = now.strftime("%Y-%m")
        (today_cost,) = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM agent_runs "
            "WHERE substr(started_at, 1, 10) = ?",
            (today,),
        ).fetchone()
        (month_cost,) = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM agent_runs "
            "WHERE substr(started_at, 1, 7) = ?",
            (month,),
        ).fetchone()
        (total,) = con.execute("SELECT COUNT(*) FROM agent_runs").fetchone()
        return {
            "today": float(today_cost),
            "month": float(month_cost),
            "runs": int(total),
        }
    except sqlite3.Error:
        return {"today": 0.0, "month": 0.0, "runs": 0}
    finally:
        con.close()


def snapshot_text(db=DB_PATH):
    """A one-shot plain-text rendering, used for --plain and as the fallback."""
    lines = ["=== hearth dashboard ===", "", "SYSTEM"]
    for label, state in system_status():
        lines.append("  {:10} {}".format(label, state))
    lines.append("  {:10} {}".format("agents", running_agents()))

    models = model_status()
    lines.append("")
    lines.append("MODELS")
    if models:
        lines.extend("  " + name for name in models)
    else:
        lines.append("  (none, or Ollama unreachable)")

    spend = spend_summary(db)
    lines.append("")
    lines.append(
        "SPEND  today ${:.2f}   month ${:.2f}   ({} runs total)".format(
            spend["today"], spend["month"], spend["runs"]
        )
    )

    lines.append("")
    lines.append("RECENT RUNS")
    rows = recent_runs(db, 10)
    if not rows:
        lines.append("  (no runs yet)")
    else:
        for started, name, model, tin, tout, latency, _cost, err in rows:
            status = "ERR" if err else "ok"
            lines.append(
                "  {}  {}  {}  {}/{} tok  {} ms  {}".format(
                    started, name, model, tin, tout, latency, status
                )
            )
    return "\n".join(lines)


def make_app(db=DB_PATH):
    """Build and return the Textual app. Textual is imported here so the rest of
    this module works without it installed."""
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical
    from textual.widgets import DataTable, Footer, Header, Static

    class HearthDashboard(App):
        TITLE = "hearth"
        CSS = """
        .sidebar { width: 34; }
        .panel { border: round $accent; padding: 0 1; margin: 0 1 1 0; }
        .title { text-style: bold; padding: 0 1; }
        """
        BINDINGS = [
            ("q", "quit", "Quit"),
            ("r", "refresh", "Refresh"),
        ]

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(classes="sidebar"):
                    yield Static(id="system", classes="panel")
                    yield Static(id="models", classes="panel")
                    yield Static(id="spend", classes="panel")
                with Vertical():
                    yield Static("RECENT RUNS", classes="title")
                    yield DataTable(id="runs")
            yield Footer()

        def on_mount(self) -> None:
            table = self.query_one("#runs", DataTable)
            table.add_columns(
                "time", "agent", "model", "in", "out", "ms", "cost", "status"
            )
            self.refresh_data()
            self.set_interval(5.0, self.refresh_data)

        def action_refresh(self) -> None:
            self.refresh_data()

        def action_quit(self) -> None:
            self.exit()

        def refresh_data(self) -> None:
            sys_text = "SYSTEM\n" + "\n".join(
                "{:10} {}".format(label, state) for label, state in system_status()
            )
            sys_text += "\n{:10} {}".format("agents", running_agents())
            self.query_one("#system", Static).update(sys_text)

            models = model_status()
            self.query_one("#models", Static).update(
                "MODELS\n" + ("\n".join(models) if models else "(none)")
            )

            spend = spend_summary(db)
            self.query_one("#spend", Static).update(
                "SPEND\ntoday  ${:.2f}\nmonth  ${:.2f}\nruns   {}".format(
                    spend["today"], spend["month"], spend["runs"]
                )
            )

            table = self.query_one("#runs", DataTable)
            table.clear()
            for started, name, model, tin, tout, latency, cost, err in recent_runs(
                db, 20
            ):
                clock = started[11:19] if started and len(started) >= 19 else "?"
                table.add_row(
                    clock,
                    name,
                    model,
                    str(tin),
                    str(tout),
                    str(latency),
                    "${:.2f}".format(cost),
                    "ERR" if err else "ok",
                )

    return HearthDashboard()


def run_tui(db=DB_PATH):
    make_app(db).run()


def self_test():
    import tempfile

    workdir = tempfile.mkdtemp(prefix="hearth-dash-selftest-")
    db = os.path.join(workdir, "audit.db")
    con = sqlite3.connect(db)
    con.executescript(
        """
        CREATE TABLE agent_runs (
          id INTEGER PRIMARY KEY, agent_name TEXT, run_id TEXT,
          started_at TEXT, finished_at TEXT, tokens_in INTEGER,
          tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER,
          error TEXT, model TEXT
        );
        """
    )
    today = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
    con.executemany(
        "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
        "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("demo", "a1", today, today, 10, 20, 0.0, 800, None, "llama3.2:3b"),
            ("demo", "a2", today, today, 5, 8, 0.0, 400, "boom", "mistral:7b"),
        ],
    )
    con.commit()
    con.close()

    rows = recent_runs(db, 10)
    assert len(rows) == 2, rows
    spend = spend_summary(db)
    assert spend["runs"] == 2, spend
    assert abs(spend["today"]) < 1e-9, spend
    text = snapshot_text(db)
    assert "RECENT RUNS" in text and "demo" in text, text
    print("hearth-dashboard self-test OK")
    print(text)
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="hearth-dashboard",
        description="hearth system dashboard (Textual TUI).",
    )
    parser.add_argument("--db", default=DB_PATH)
    parser.add_argument(
        "--plain", action="store_true", help="print a text snapshot and exit"
    )
    parser.add_argument(
        "--self-test", action="store_true", help="exercise the data layer and exit"
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if args.plain:
        print(snapshot_text(args.db))
        return 0

    try:
        run_tui(args.db)
        return 0
    except Exception as exc:  # noqa: BLE001 - fall back to plain text on any TUI error
        print("hearth-dashboard: TUI unavailable ({}). Falling back to text.".format(exc), file=sys.stderr)
        print(snapshot_text(args.db))
        return 0


if __name__ == "__main__":
    sys.exit(main())
