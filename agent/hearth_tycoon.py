#!/usr/bin/env python3
"""hearth tycoon: the facility as a tycoon game, computed from the audit db.

v1.7 "Tycoon" turns the map into a place you can read at a glance. This module
is the read side of that: pure functions over the audit database that the map
backend (webui/hearth_mapd.py) serves as JSON. Nothing here calls a model, so
the whole spectacle costs zero tokens.

  buildings(db)      one building per agent family (the run name with its
                     random suffix stripped), sized by lifetime runs, with the
                     tokens it burned, errors, models, and whether a worker is
                     on shift right now. The retired growth loop shows up as a
                     shuttered workshop with its lifetime ledger.
  achievements(db)   milestones unlocked by what the facility has actually
                     done: first run, run counts, tokens, dollars saved versus
                     the cloud, the egress wall holding, a tripwire catch, a
                     flight-recorded run, a 7-day streak, and more. Each one
                     carries its progress and, where the audit log can say,
                     when it was unlocked.
  bill_of_materials  merges the build-time bill of materials the NixOS module
                     renders (/etc/hearth/sbom.json: flake inputs with their
                     narHash, every system package with its store path, the
                     hearth modules switched on) with the live model digests
                     Ollama reports, so every model is pinned too.

Only small tables are read (agent_runs, egress_log, tripwires, run_steps,
learnings, agent_state). agent_events and agent_transcript can hold millions of
rows and are never scanned. Every reader opens the db read-only and degrades to
an empty answer instead of raising. Standard library only.
"""

import argparse
import json
import math
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_SBOM = "/etc/hearth/sbom.json"

# Blended frontier-cloud price per million tokens. Matches the map backend's
# "saved" counter (HEARTH_CLOUD_PRICE_MTOK) so both numbers agree.
CLOUD_PRICE_PER_MTOK = float(os.environ.get("HEARTH_CLOUD_PRICE_MTOK", "15.0"))

# A worker counts as on shift when its state is live and was touched recently.
LIVE_STATES = ("SPAWNING", "THINKING", "TOOL_CALL", "WAITING_IO", "WAITING_APPROVAL")
LIVE_WINDOW_S = 15 * 60

MAX_BUILDINGS = 8
MAX_FLOORS = 6

_SUFFIX = re.compile(r"(-[0-9a-f]{6,}|-\d+)$")


def family_of(name):
    """The building an agent belongs to: its name, lowercased, with the random
    run suffix (web-1ec9641b, manager-1) stripped. The map's JavaScript mirrors
    this exactly so live sprites walk to the right door."""
    s = (name or "").strip().lower()
    prev = None
    while s and s != prev:
        prev = s
        s = _SUFFIX.sub("", s)
    return s or "unnamed"


def floors_for(runs):
    """Building height grows with the log of lifetime runs: 1 run is a shed,
    ~32 runs is a six-storey block. Logarithmic so one busy agent does not
    dwarf the skyline."""
    if runs <= 0:
        return 1
    return max(1, min(MAX_FLOORS, 1 + int(math.log2(runs))))


def saved_usd(tokens):
    return round((tokens or 0) / 1_000_000.0 * CLOUD_PRICE_PER_MTOK, 2)


def _open_ro(db):
    if not db or not os.path.exists(db):
        return None
    try:
        con = sqlite3.connect("file:{}?mode=ro".format(db), uri=True, timeout=10)
        con.execute("PRAGMA query_only=ON")
        return con
    except sqlite3.Error:
        return None


def _has_table(con, name):
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                           (name,)).fetchone() is not None
    except sqlite3.Error:
        return False


def _parse_ts(ts):
    """ISO timestamp from the audit log to an aware UTC datetime, or None.
    Rows written by different versions carry +00:00, Z, or no zone at all;
    a zone-less stamp is treated as UTC, which is what the writers use."""
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


# --------------------------------------------------------------------- buildings

def buildings(db, now=None, grow_active=False, limit=MAX_BUILDINGS):
    """The skyline: [{family, runs, tokens, errors, models, last_run, floors,
    workers, saved_usd, kind}], busiest first, plus the growth workshop when
    the growth loop has ever run. `now` is injectable for tests."""
    now = now or datetime.now(timezone.utc)
    con = _open_ro(db)
    if con is None:
        return []
    fam = {}
    try:
        if _has_table(con, "agent_runs"):
            for name, tin, tout, err, model, started in con.execute(
                    "SELECT agent_name, COALESCE(tokens_in,0), COALESCE(tokens_out,0), "
                    "error, model, started_at FROM agent_runs"):
                f = family_of(name)
                b = fam.setdefault(f, {"family": f, "runs": 0, "tokens": 0, "errors": 0,
                                       "models": set(), "last_run": "", "workers": 0})
                b["runs"] += 1
                b["tokens"] += int(tin) + int(tout)
                if err:
                    b["errors"] += 1
                if model:
                    b["models"].add(model)
                if started and str(started) > b["last_run"]:
                    b["last_run"] = str(started)
        # Workers on shift: live states touched in the last few minutes.
        if _has_table(con, "agent_state"):
            for agent_id, state, updated in con.execute(
                    "SELECT agent_id, state, updated_at FROM agent_state"):
                if state not in LIVE_STATES:
                    continue
                t = _parse_ts(updated)
                if t is None or (now - t).total_seconds() > LIVE_WINDOW_S:
                    continue
                if (agent_id or "").startswith("daemon-grow"):
                    continue  # the growth loop is the workshop below, not a building
                f = family_of(agent_id)
                b = fam.setdefault(f, {"family": f, "runs": 0, "tokens": 0, "errors": 0,
                                       "models": set(), "last_run": "", "workers": 0})
                b["workers"] += 1
        workshop = None
        if _has_table(con, "learnings"):
            row = con.execute(
                "SELECT COALESCE(SUM(kind='success'),0), COALESCE(SUM(kind='failure'),0), "
                "MIN(ts), MAX(ts) FROM learnings WHERE source='grow'").fetchone()
            if row and (row[0] or row[1]):
                workshop = {"family": "growth workshop", "kind": "workshop",
                            "validated": int(row[0]), "failed": int(row[1]),
                            "first": row[2] or "", "last": row[3] or "",
                            "state": "running" if grow_active else "shuttered"}
    except sqlite3.Error:
        return []
    finally:
        con.close()

    out = []
    ranked = sorted(fam.values(), key=lambda b: (b["workers"] > 0, b["runs"], b["last_run"]),
                    reverse=True)
    for b in ranked[:limit]:
        out.append({"family": b["family"], "kind": "agent", "runs": b["runs"],
                    "tokens": b["tokens"], "errors": b["errors"],
                    "models": sorted(b["models"]), "last_run": b["last_run"],
                    "floors": floors_for(b["runs"]), "workers": b["workers"],
                    "saved_usd": saved_usd(b["tokens"])})
    if workshop:
        attempts = workshop["validated"] + workshop["failed"]
        workshop["floors"] = floors_for(attempts // 100)
        workshop["pass_rate"] = round(workshop["validated"] / attempts, 3) if attempts else 0.0
        out.append(workshop)
    return out


# ------------------------------------------------------------------ achievements

def _nth_ts(con, sql, n, params=()):
    """Timestamp of the n-th row (1-based) of an ordered query, or ''. Used to
    say *when* a count-based achievement was crossed."""
    try:
        row = con.execute(sql + " LIMIT 1 OFFSET ?", tuple(params) + (n - 1,)).fetchone()
        return (row[0] or "") if row else ""
    except sqlite3.Error:
        return ""


def _longest_streak(days):
    """Longest run of consecutive calendar days in a set of date objects,
    and the day that streak reached its length."""
    best, best_end, cur, prev = 0, None, 0, None
    for d in sorted(days):
        cur = cur + 1 if prev is not None and d - prev == timedelta(days=1) else 1
        if cur > best:
            best, best_end = cur, d
        prev = d
    return best, best_end


def _tokens_crossed(rows, goal):
    """started_at of the run whose cumulative tokens first reached goal."""
    total = 0
    for started, tokens in rows:
        total += tokens
        if total >= goal:
            return started or ""
    return ""


def achievements(db, local_tz=None):
    """[{id, title, desc, icon, unlocked, at, progress: [have, goal]}], in
    display order. `local_tz` decides what counts as the night shift (the box's
    own clock by default)."""
    local_tz = local_tz or datetime.now().astimezone().tzinfo
    con = _open_ro(db)
    runs = []          # (started_at, tokens, error, model)
    egress_blocked = trips = recorded = 0
    first_block = first_trip = first_recorded = ""
    grow_validated = 0
    first_grow = ""
    if con is not None:
        try:
            if _has_table(con, "agent_runs"):
                runs = [(r[0] or "", int(r[1] or 0), r[2], r[3]) for r in con.execute(
                    "SELECT started_at, COALESCE(tokens_in,0)+COALESCE(tokens_out,0), error, model "
                    "FROM agent_runs ORDER BY started_at, id")]
            if _has_table(con, "egress_log"):
                egress_blocked = con.execute(
                    "SELECT COUNT(*) FROM egress_log WHERE allowed=0").fetchone()[0]
                first_block = _nth_ts(con, "SELECT ts FROM egress_log WHERE allowed=0 ORDER BY id", 1)
            if _has_table(con, "tripwires"):
                trips = con.execute("SELECT COUNT(*) FROM tripwires").fetchone()[0]
                first_trip = _nth_ts(con, "SELECT ts FROM tripwires ORDER BY id", 1)
            if _has_table(con, "run_steps"):
                recorded = con.execute(
                    "SELECT COUNT(DISTINCT agent_id) FROM run_steps").fetchone()[0]
                first_recorded = _nth_ts(con, "SELECT ts FROM run_steps ORDER BY id", 1)
            if _has_table(con, "learnings"):
                grow_validated = con.execute(
                    "SELECT COUNT(*) FROM learnings WHERE source='grow' AND kind='success'"
                ).fetchone()[0]
                first_grow = _nth_ts(con, "SELECT ts FROM learnings WHERE source='grow' "
                                          "AND kind='success' ORDER BY id", 1)
        except sqlite3.Error:
            pass
        finally:
            con.close()

    n = len(runs)
    tokens = sum(r[1] for r in runs)
    models = []
    for r in runs:
        if r[3] and r[3] not in models:
            models.append(r[3])
    night = [r[0] for r in runs
             if _parse_ts(r[0]) and _parse_ts(r[0]).astimezone(local_tz).hour < 5]
    days = {_parse_ts(r[0]).astimezone(local_tz).date() for r in runs if _parse_ts(r[0])}
    streak, streak_end = _longest_streak(days)
    # Longest run of consecutive clean (error-free) runs, and where it peaked.
    clean_best, clean_cur, clean_at = 0, 0, ""
    for r in runs:
        clean_cur = 0 if r[2] else clean_cur + 1
        if clean_cur > clean_best:
            clean_best, clean_at = clean_cur, r[0]

    def at_run(k):
        return runs[k - 1][0] if n >= k else ""

    def tok_at(goal):
        return _tokens_crossed([(r[0], r[1]) for r in runs], goal)

    usd = saved_usd(tokens)
    usd_goal_tokens = int(25 * 1_000_000 / CLOUD_PRICE_PER_MTOK) if CLOUD_PRICE_PER_MTOK else 0
    third_model_at = ""
    if len(models) >= 3:
        for r in runs:
            if r[3] == models[2]:
                third_model_at = r[0]
                break

    spec = [
        ("first-light", "First light", "the facility ran its first agent", "\U0001F56F",
         n, 1, at_run(1)),
        ("busy-floor", "Busy floor", "25 runs on the books", "\U0001F3ED",
         n, 25, at_run(25)),
        ("centurion", "Centurion", "100 runs, every one in the audit log", "\U0001F3DB",
         n, 100, at_run(100)),
        ("megawatt", "Megawatt", "a million tokens processed on local silicon", "⚡",
         tokens, 1_000_000, tok_at(1_000_000)),
        ("cloud-dodger", "Cloud dodger",
         "$25 of frontier-cloud spend avoided (${:,.2f} so far)".format(usd), "\U0001F4B0",
         int(usd * 100), 2500, tok_at(usd_goal_tokens) if usd_goal_tokens else ""),
        ("polyglot", "Polyglot", "three different local models have done real work",
         "\U0001F9E0", len(models), 3, third_model_at),
        ("night-shift", "Night shift", "a run started between midnight and 5am",
         "\U0001F319", len(night), 1, night[0] if night else ""),
        ("week-streak", "Seven-day streak", "runs on seven consecutive days",
         "\U0001F4C5", streak, 7, streak_end.isoformat() if streak >= 7 and streak_end else ""),
        ("clean-sheet", "Clean sheet", "20 runs in a row without an error",
         "✨", clean_best, 20, clean_at if clean_best >= 20 else ""),
        ("wall-holds", "The wall holds", "the egress wall bounced an outbound connection",
         "\U0001F9F1", egress_blocked, 1, first_block),
        ("honeypot", "Honeypot", "a tripwire caught an agent reading a decoy",
         "\U0001F6A8", trips, 1, first_trip),
        ("black-box", "Black box", "a run was captured by the flight recorder",
         "\U0001F4FC", recorded, 1, first_recorded),
        ("self-made", "Self-made", "the growth loop validated its own improvement",
         "\U0001F527", grow_validated, 1, first_grow),
    ]
    out = []
    for aid, title, desc, icon, have, goal, at in spec:
        unlocked = have >= goal
        out.append({"id": aid, "title": title, "desc": desc, "icon": icon,
                    "unlocked": unlocked, "at": at if unlocked else "",
                    "progress": [min(have, goal), goal]})
    return out


# -------------------------------------------------------------- bill of materials

def _parse_ollama_tags(text):
    try:
        data = json.loads(text or "{}")
    except (TypeError, ValueError):
        return []
    models = []
    for m in data.get("models") or []:
        det = m.get("details") or {}
        models.append({"name": m.get("name") or m.get("model") or "?",
                       "digest": m.get("digest") or "",
                       "size": int(m.get("size") or 0),
                       "family": det.get("family") or "",
                       "parameters": det.get("parameter_size") or "",
                       "quantization": det.get("quantization_level") or "",
                       "modified": m.get("modified_at") or ""})
    return sorted(models, key=lambda x: x["name"])


def bill_of_materials(sbom_path=DEFAULT_SBOM, ollama_tags_text=None):
    """The build-time SBOM plus live model digests. Either half may be
    missing (not built with the module, Ollama down); the page shows what it
    has and says which half is absent."""
    doc = {}
    try:
        with open(sbom_path, "r", encoding="utf-8") as fh:
            doc = json.load(fh)
    except (OSError, ValueError):
        doc = {}
    out = {
        "built": bool(doc),
        "system": doc.get("system") or {},
        "inputs": doc.get("inputs") or [],
        "modules": doc.get("modules") or [],
        "services": doc.get("services") or [],
        "packages": doc.get("packages") or [],
        "models": _parse_ollama_tags(ollama_tags_text) if ollama_tags_text else [],
        "models_live": ollama_tags_text is not None,
    }
    out["counts"] = {k: len(out[k]) for k in ("inputs", "modules", "services",
                                               "packages", "models")}
    return out


# --------------------------------------------------------------------- self-test

def _self_test():
    import tempfile
    assert family_of("web-1ec9641b") == "web"
    assert family_of("wall-ab-76f7ba9c") == "wall-ab"
    assert family_of("manager-1") == "manager"
    assert family_of("Daily-Pulse") == "daily-pulse"
    assert family_of("") == "unnamed"
    assert family_of("chat") == "chat"
    assert [floors_for(r) for r in (0, 1, 2, 4, 31, 32, 10_000)] == [1, 1, 2, 3, 5, 6, 6]
    assert _longest_streak(set())[0] == 0

    d = tempfile.mkdtemp(prefix="hearth-tycoon-")
    db = os.path.join(d, "audit.db")
    assert buildings(db) == [] and bill_of_materials(os.path.join(d, "none"))["built"] is False
    con = sqlite3.connect(db)
    con.executescript("""
      CREATE TABLE agent_runs (id INTEGER PRIMARY KEY, agent_name TEXT, run_id TEXT,
        started_at TEXT, finished_at TEXT, tokens_in INTEGER, tokens_out INTEGER,
        cost_usd REAL, latency_ms INTEGER, error TEXT, model TEXT);
      CREATE TABLE agent_state (agent_id TEXT PRIMARY KEY, state TEXT, detail TEXT, updated_at TEXT);
      CREATE TABLE egress_log (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, ts TEXT,
        tool TEXT, host TEXT, url TEXT, allowed INTEGER);
      CREATE TABLE tripwires (id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, ts TEXT,
        tool TEXT, path TEXT, token TEXT, detail TEXT);
      CREATE TABLE learnings (id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, kind TEXT,
        topic TEXT, insight TEXT, tags TEXT, source TEXT);
    """)
    base = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(30):  # 30 runs over 8 consecutive days, three models
        fam = "web" if i % 3 else "daily-pulse"
        started = (base + timedelta(days=i // 4, minutes=i)).isoformat()
        rows.append((fam + "-{:08x}".format(i + 0xabc0000), "r%d" % i, started, started,
                     20000, 20000, 0.0, 5, "boom" if i == 3 else None,
                     ("llama3.2:3b", "qwen2.5-coder", "mistral:7b")[i % 3]))
    rows.append(("owl", "night", "2026-09-10T02:30:00", "2026-09-10T02:31:00",
                 10, 10, 0.0, 5, None, "llama3.2:3b"))
    con.executemany("INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
                    "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    now = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
    con.executemany("INSERT INTO agent_state VALUES (?,?,?,?)", [
        ("web-deadbeef", "TOOL_CALL", "", (now - timedelta(minutes=1)).isoformat()),
        ("web-cafef00d", "THINKING", "", (now - timedelta(hours=3)).isoformat()),  # stale
        ("scout-11111111", "THINKING", "", now.isoformat()),   # new family, no runs yet
        ("web-00000001", "DONE", "", now.isoformat()),          # not live
        ("daemon-grow-c8", "TOOL_CALL", "", now.isoformat()),   # workshop, not a building
    ])
    con.execute("INSERT INTO egress_log (agent_id, ts, tool, host, url, allowed) "
                "VALUES ('web-1','2026-09-05T00:00:00','fetch','evil.test','',0)")
    con.executemany("INSERT INTO learnings (ts, kind, topic, insight, tags, source) "
                    "VALUES (?,?,?,?,?,?)",
                    [("2026-09-02T00:00:00", "success", "t", "ok", "", "grow"),
                     ("2026-09-03T00:00:00", "failure", "t", "no", "", "grow"),
                     ("2026-09-04T00:00:00", "failure", "t", "no", "", "grow"),
                     ("2026-09-04T00:00:00", "lesson", "t", "x", "", "tool")])
    con.commit()
    con.close()

    b = buildings(db, now=now)
    by = {x["family"]: x for x in b}
    assert b[0]["family"] in ("web", "scout"), b  # buildings with workers lead
    assert by["web"]["runs"] == 20 and by["web"]["workers"] == 1, by["web"]
    assert by["web"]["errors"] == 0 and by["daily-pulse"]["errors"] == 1, by
    assert by["web"]["tokens"] == 20 * 40000 and by["web"]["saved_usd"] == 12.0, by["web"]
    assert by["web"]["floors"] == floors_for(20) == 5
    assert set(by["web"]["models"]) == {"qwen2.5-coder", "mistral:7b"}, by["web"]
    assert by["scout"]["runs"] == 0 and by["scout"]["workers"] == 1, by["scout"]
    assert not any(f.startswith("daemon-grow") for f in by), by
    ws = by["growth workshop"]
    assert ws["kind"] == "workshop" and ws["state"] == "shuttered", ws
    assert ws["validated"] == 1 and ws["failed"] == 2 and ws["pass_rate"] == 0.333, ws
    assert buildings(db, now=now, grow_active=True)[-1]["state"] == "running"
    assert len(buildings(db, now=now, limit=1)) == 2  # one agent building + workshop

    ach = {a["id"]: a for a in achievements(db, local_tz=timezone.utc)}
    assert ach["first-light"]["unlocked"] and ach["first-light"]["at"].startswith("2026-09-01")
    assert ach["busy-floor"]["unlocked"] and not ach["centurion"]["unlocked"]
    assert ach["centurion"]["progress"] == [31, 100] and ach["centurion"]["at"] == ""
    assert ach["megawatt"]["unlocked"] and ach["megawatt"]["progress"] == [1_000_000, 1_000_000]
    assert ach["polyglot"]["unlocked"] and ach["night-shift"]["unlocked"]
    assert ach["night-shift"]["at"] == "2026-09-10T02:30:00", ach["night-shift"]
    assert ach["week-streak"]["unlocked"] and ach["week-streak"]["progress"][0] == 7
    assert ach["clean-sheet"]["unlocked"], ach["clean-sheet"]  # 27 clean after the error
    assert ach["wall-holds"]["unlocked"] and not ach["honeypot"]["unlocked"]
    assert not ach["black-box"]["unlocked"] and ach["self-made"]["unlocked"]
    assert ach["cloud-dodger"]["progress"][1] == 2500
    assert all(set(a) == {"id", "title", "desc", "icon", "unlocked", "at", "progress"}
               for a in ach.values())
    # an empty db unlocks nothing and never raises
    assert not any(a["unlocked"] for a in achievements(os.path.join(d, "missing.db")))

    sb = os.path.join(d, "sbom.json")
    with open(sb, "w") as fh:
        json.dump({"system": {"hostName": "t"}, "inputs": [{"name": "nixpkgs"}],
                   "modules": [{"name": "egress", "enabled": True}], "services": [],
                   "packages": [{"name": "git", "version": "2", "path": "/nix/store/x-git"}]}, fh)
    tags = json.dumps({"models": [{"name": "z:1", "digest": "abc", "size": 5,
                                   "details": {"family": "llama", "parameter_size": "3B",
                                               "quantization_level": "Q4"}},
                                  {"name": "a:1", "digest": "def", "size": 7}]})
    bom = bill_of_materials(sb, tags)
    assert bom["built"] and bom["models_live"] and bom["system"]["hostName"] == "t"
    assert [m["name"] for m in bom["models"]] == ["a:1", "z:1"], bom["models"]
    assert bom["models"][1]["quantization"] == "Q4" and bom["counts"]["packages"] == 1
    assert bill_of_materials(sb, None)["models_live"] is False
    assert bill_of_materials(sb, "garbage")["models"] == []
    print("hearth-tycoon self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-tycoon")
    p.add_argument("what", nargs="?", choices=("buildings", "achievements", "sbom"))
    p.add_argument("--db", default=os.environ.get("HEARTH_DB", DEFAULT_DB))
    p.add_argument("--sbom", default=os.environ.get("HEARTH_SBOM", DEFAULT_SBOM))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.what == "buildings":
        print(json.dumps(buildings(a.db), indent=2))
    elif a.what == "achievements":
        for x in achievements(a.db):
            mark = "x" if x["unlocked"] else " "
            print("[{}] {:<18} {:>8}/{:<8} {}".format(mark, x["title"], x["progress"][0],
                                                      x["progress"][1], x["desc"]))
    elif a.what == "sbom":
        print(json.dumps(bill_of_materials(a.sbom)["counts"], indent=2))
    else:
        p.error("nothing to do (buildings | achievements | sbom, or --self-test)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
