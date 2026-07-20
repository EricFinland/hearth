#!/usr/bin/env python3
"""hearth scheduler: standing missions that run on their own.

A registry (JSON) of missions, each with a schedule. A periodic `--tick` (driven
by a systemd timer) finds the missions that are due and dispatches them by
dropping a queue file for the normal spawn path, then records when each last ran.
This is the "works while you sleep" layer: point it at a goal and a cadence and
hearth keeps doing it.

Schedule forms:
  {"every_minutes": 60}   run roughly every N minutes
  {"at": "09:00"}         run once per day, the first tick at or after HH:MM (local)

Missions come from two places: the mutable registry (schedule.json, edited via
add_mission and the webui) and an optional read-only declarative file rendered
by the system configuration (/etc/hearth/missions.json). Declarative missions
get ids of the form "nix-<name>" and stamp their last-run times in a sidecar
state file, never in the config itself. On id collision the declarative entry
wins.

The schedule math is pure and injectable (you pass `now`), so it is fully
testable with no clock, no systemd, and no Ollama. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime, timedelta

# Lives in an operator-owned subdir: operator (who runs the scheduler and mapd)
# can write here, but not /var/lib/hearth itself (0750 hearth).
DEFAULT_REGISTRY = os.environ.get("HEARTH_SCHEDULE", "/var/lib/hearth/scheduler/schedule.json")
# Read-only declarative missions rendered by the system configuration, plus the
# writable sidecar where their last-run stamps live.
DEFAULT_MISSIONS = os.environ.get("HEARTH_MISSIONS", "/etc/hearth/missions.json")
DEFAULT_MISSIONS_STATE = os.environ.get(
    "HEARTH_MISSIONS_STATE", "/var/lib/hearth/scheduler/declarative-state.json")
QUEUE_DIR = "/var/lib/hearth/queue"
SYSTEMCTL = "/run/current-system/sw/bin/systemctl"
SUDO = "/run/wrappers/bin/sudo"


def load_registry(path=DEFAULT_REGISTRY):
    """Return the list of missions, or [] if the file is missing or unreadable."""
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, list) else []
    except (OSError, ValueError):
        return []


def save_registry(missions, path=DEFAULT_REGISTRY):
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(missions, fh, indent=2)
    os.replace(tmp, path)


def add_mission(mission, path=DEFAULT_REGISTRY):
    """Append a mission (assigning an id) and persist. Returns the id."""
    missions = load_registry(path)
    mid = mission.get("id") or ("m-" + uuid.uuid4().hex[:8])
    mission["id"] = mid
    mission.setdefault("enabled", True)
    mission.setdefault("last_run", None)
    missions.append(mission)
    save_registry(missions, path)
    return mid


def load_declarative(path=None):
    """Return declarative missions from the read-only config file, or [] if the
    file is missing or invalid. Each entry gets id "nix-<name>" and a
    "source": "nix" marker. Entries without name/prompt/schedule are skipped."""
    path = path or DEFAULT_MISSIONS
    if not os.path.exists(path):
        return []
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for entry in data:
        if not isinstance(entry, dict) or not (
                entry.get("name") and entry.get("prompt") and entry.get("schedule")):
            sys.stderr.write(
                "hearth-schedule: skipping invalid declarative entry: {!r}\n".format(
                    entry.get("name") if isinstance(entry, dict) else entry))
            continue
        m = dict(entry)
        m["id"] = "nix-{}".format(entry["name"])
        m["source"] = "nix"
        m.setdefault("enabled", True)
        out.append(m)
    return out


def load_declarative_state(path=None):
    """Sidecar dict {mission_id: iso_ts} of declarative last-run stamps."""
    path = path or DEFAULT_MISSIONS_STATE
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_declarative_state(state, path=None):
    """Persist the sidecar atomically. Best-effort: failures are swallowed so a
    read-only or missing state dir never stops the tick."""
    path = path or DEFAULT_MISSIONS_STATE
    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(state, fh, indent=2)
        os.replace(tmp, path)
    except OSError:
        pass


def _parse(ts):
    try:
        return datetime.fromisoformat(ts) if ts else None
    except (TypeError, ValueError):
        return None


def is_due(mission, now, last_run):
    """Pure: is this mission due to run at `now` given its last run time?"""
    if not mission.get("enabled", True):
        return False
    sched = mission.get("schedule") or {}
    every = sched.get("every_minutes")
    if every:
        if last_run is None:
            return True
        return (now - last_run).total_seconds() >= float(every) * 60
    at = sched.get("at")
    if at:
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
        except (ValueError, AttributeError):
            return False
        at_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < at_today:
            return False  # not time yet today
        # due if we have not already run on or after today's scheduled time
        return last_run is None or last_run < at_today
    return False


def due_missions(missions, now):
    return [m for m in missions if is_due(m, now, _parse(m.get("last_run")))]


def next_due(mission, now, last_run):
    """Human-readable next-due info for listings. Pure."""
    if not mission.get("enabled", True):
        return "disabled"
    if is_due(mission, now, last_run):
        return "now"
    sched = mission.get("schedule") or {}
    every = sched.get("every_minutes")
    if every and last_run is not None:
        return (last_run + timedelta(minutes=float(every))).isoformat(sep=" ", timespec="minutes")
    at = sched.get("at")
    if at:
        try:
            hh, mm = (int(x) for x in at.split(":", 1))
        except (ValueError, AttributeError):
            return "?"
        at_today = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now < at_today:
            return at_today.isoformat(sep=" ", timespec="minutes")
        return (at_today + timedelta(days=1)).isoformat(sep=" ", timespec="minutes")
    return "?"


def _csv(value):
    """Normalize list-or-string to the comma-joined form the queue consumer
    expects (same shape hearth_mapd writes)."""
    if isinstance(value, list):
        return ",".join(str(v) for v in value)
    return value or ""


def _kick_spawn():
    for args in (["reset-failed", "hearth-spawn.path", "hearth-spawn.service"],
                 ["start", "--no-block", "hearth-spawn.service"]):
        try:
            subprocess.run([SUDO, "-n", SYSTEMCTL] + args, capture_output=True, text=True, timeout=12)
        except (OSError, subprocess.SubprocessError):
            pass


def dispatch(mission, queue_dir=QUEUE_DIR, kick=True):
    """Drop a queue file so the normal spawn path runs this mission. Returns the
    run id. kind: 'agent' (default) | 'swarm' | 'marathon'."""
    kind = mission.get("kind", "agent")
    name = (mission.get("name") or "sched").replace("/", "_").replace(" ", "_")[:40] or "sched"
    run_id = "{}-{}".format(name, uuid.uuid4().hex[:8])
    req = {
        "name": run_id, "model": mission.get("model") or "qwen2.5-coder",
        "prompt": mission.get("prompt") or mission.get("goal") or "",
        "mode": mission.get("mode") or "bypass",
        "creds": _csv(mission.get("creds")),
        "tools": _csv(mission.get("tools")),
        "allowed_hosts": _csv(mission.get("allowed_hosts")),
        "swarm": kind == "swarm", "marathon": kind == "marathon",
        "checkin": False, "evolve": False, "grow": False,
    }
    os.makedirs(queue_dir, exist_ok=True)
    tmp = os.path.join(queue_dir, run_id + ".json.tmp")
    final = os.path.join(queue_dir, run_id + ".json")
    with open(tmp, "w") as fh:
        json.dump(req, fh)
    os.replace(tmp, final)
    if kick:
        _kick_spawn()
    return run_id


def tick(path=DEFAULT_REGISTRY, now=None, dispatch_fn=None, missions_path=None, state_path=None):
    """Dispatch every due mission and stamp its last_run. Considers both the
    mutable registry (stamps in the registry, as always) and the declarative
    missions file (stamps in the sidecar state, since the config is read-only).
    Returns the list of (mission_id, run_id) dispatched."""
    now = now or datetime.now()
    dispatch_fn = dispatch_fn or dispatch
    declarative = load_declarative(missions_path)
    declared_ids = set(m["id"] for m in declarative)
    missions = load_registry(path)
    fired = []
    changed = False
    for m in missions:
        if m.get("id") in declared_ids:
            sys.stderr.write(
                "hearth-schedule: registry mission {} shadowed by declarative entry\n".format(
                    m.get("id")))
            continue
        if is_due(m, now, _parse(m.get("last_run"))):
            try:
                run_id = dispatch_fn(m)
            except Exception:  # noqa: BLE001 - one bad mission must not stop the rest
                continue
            m["last_run"] = now.isoformat()
            changed = True
            fired.append((m["id"], run_id))
    if changed:
        save_registry(missions, path)
    if declarative:
        state = load_declarative_state(state_path)
        state_changed = False
        for m in declarative:
            if is_due(m, now, _parse(state.get(m["id"]))):
                try:
                    run_id = dispatch_fn(m)
                except Exception:  # noqa: BLE001
                    continue
                state[m["id"]] = now.isoformat()
                state_changed = True
                fired.append((m["id"], run_id))
        if state_changed:
            save_declarative_state(state, state_path)
    return fired


def _self_test():
    import tempfile
    from datetime import timedelta
    d = tempfile.mkdtemp(prefix="hearth-sched-")
    reg = os.path.join(d, "schedule.json")

    assert load_registry(reg) == [], "missing registry -> empty"
    mid = add_mission({"name": "daily digest", "goal": "summarize", "kind": "marathon",
                       "schedule": {"every_minutes": 60}}, path=reg)
    assert mid and load_registry(reg)[0]["name"] == "daily digest"

    base = datetime(2026, 7, 1, 9, 0, 0)
    # every_minutes: due when never run, due after the interval, not before
    m_every = {"id": "a", "enabled": True, "schedule": {"every_minutes": 30}}
    assert is_due(m_every, base, None) is True, "never run -> due"
    assert is_due(m_every, base, base - timedelta(minutes=10)) is False, "10<30 min -> not due"
    assert is_due(m_every, base, base - timedelta(minutes=40)) is True, "40>=30 min -> due"
    # disabled never fires
    assert is_due({"enabled": False, "schedule": {"every_minutes": 1}}, base, None) is False
    # daily 'at': not before the time, due after, once per day
    m_at = {"id": "b", "enabled": True, "schedule": {"at": "09:30"}}
    assert is_due(m_at, base, None) is False, "before 09:30 -> not due"
    after = base.replace(minute=31)
    assert is_due(m_at, after, None) is True, "after 09:30, never run -> due"
    assert is_due(m_at, after, after.replace(second=1)) is False, "already ran today -> not due"
    assert is_due(m_at, after + timedelta(days=1), after) is True, "next day -> due again"

    # tick dispatches due missions, stamps last_run, and is idempotent within the window
    no_decl = os.path.join(d, "no-missions.json")  # keep ticks hermetic on real hosts
    fired_log = []
    add_mission({"id": "due1", "name": "x", "goal": "g", "schedule": {"every_minutes": 5}}, path=reg)
    out = tick(reg, now=base, dispatch_fn=lambda m: fired_log.append(m["id"]) or ("run-" + m["id"]),
               missions_path=no_decl)
    ids = [mid for mid, _ in out]
    assert "due1" in ids, ("due1 should fire", out)
    # immediately ticking again: due1 just ran, the 60-min one already fired too -> nothing new
    out2 = tick(reg, now=base, dispatch_fn=lambda m: fired_log.append(m["id"]) or "x",
                missions_path=no_decl)
    assert out2 == [], ("nothing due right after a tick", out2)
    # an hour later the 5-min mission is due again
    out3 = tick(reg, now=base + timedelta(hours=1), dispatch_fn=lambda m: "r",
                missions_path=no_decl)
    assert any(mid == "due1" for mid, _ in out3), out3

    # declarative loading: valid entries get nix- ids, invalid ones are skipped
    dm = os.path.join(d, "missions.json")
    st = os.path.join(d, "declarative-state.json")
    with open(dm, "w") as fh:
        json.dump([
            {"name": "watch", "kind": "agent", "model": "m", "prompt": "p",
             "schedule": {"every_minutes": 15}, "tools": ["read_file"],
             "allowed_hosts": ["github.com"], "creds": ["alpha"], "enabled": True},
            {"name": "off", "prompt": "p", "schedule": {"every_minutes": 1}, "enabled": False},
            {"name": "bad-no-prompt", "schedule": {"every_minutes": 1}},
        ], fh)
    decl = load_declarative(dm)
    assert [m["id"] for m in decl] == ["nix-watch", "nix-off"], decl
    assert all(m.get("source") == "nix" for m in decl)

    # a due declarative mission fires and stamps the sidecar, not the registry
    reg2 = os.path.join(d, "sched2.json")
    save_registry([], reg2)
    seen = []
    out = tick(reg2, now=base, dispatch_fn=lambda m: seen.append(m) or ("run-" + m["id"]),
               missions_path=dm, state_path=st)
    assert out == [("nix-watch", "run-nix-watch")], out
    assert load_registry(reg2) == [], "registry untouched by declarative stamps"
    state = load_declarative_state(st)
    assert state.get("nix-watch") == base.isoformat(), state
    assert "nix-off" not in state, "disabled declarative never fires"
    # not due again inside the window, due again after it
    out = tick(reg2, now=base + timedelta(minutes=5), dispatch_fn=lambda m: "r",
               missions_path=dm, state_path=st)
    assert out == [], out
    out = tick(reg2, now=base + timedelta(minutes=20), dispatch_fn=lambda m: "r",
               missions_path=dm, state_path=st)
    assert [mid for mid, _ in out] == ["nix-watch"], out

    # id collision: declarative wins, registry twin never fires or gets stamped
    save_registry([{"id": "nix-watch", "name": "impostor", "goal": "g",
                    "schedule": {"every_minutes": 1}, "enabled": True,
                    "last_run": None}], reg2)
    st2 = os.path.join(d, "state2.json")
    seen2 = []
    out = tick(reg2, now=base, dispatch_fn=lambda m: seen2.append(m) or ("run-" + m["id"]),
               missions_path=dm, state_path=st2)
    assert [mid for mid, _ in out] == ["nix-watch"], out
    assert len(seen2) == 1 and seen2[0].get("source") == "nix", "declarative wins on collision"
    assert load_registry(reg2)[0]["last_run"] is None, "shadowed registry mission untouched"

    # dispatch writes the scoping fields in normalized comma-joined form
    q = os.path.join(d, "queue")
    rid = dispatch({"name": "scoped", "kind": "agent", "prompt": "do it",
                    "tools": ["read_file", "web_get"], "allowed_hosts": "github.com",
                    "creds": ["alpha", "beta"]}, queue_dir=q, kick=False)
    with open(os.path.join(q, rid + ".json")) as fh:
        req = json.load(fh)
    assert req["tools"] == "read_file,web_get", req
    assert req["allowed_hosts"] == "github.com", req
    assert req["creds"] == "alpha,beta", req
    assert req["prompt"] == "do it", req

    # invalid declarative file: [] and tick still runs the registry
    badf = os.path.join(d, "bad-missions.json")
    with open(badf, "w") as fh:
        fh.write("{not json")
    assert load_declarative(badf) == []
    out = tick(reg2, now=base, dispatch_fn=lambda m: "r", missions_path=badf, state_path=st2)
    assert [mid for mid, _ in out] == ["nix-watch"], ("registry twin fires once nothing shadows it", out)

    print("hearth-schedule self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-schedule")
    p.add_argument("--registry", default=DEFAULT_REGISTRY)
    p.add_argument("--tick", action="store_true", help="dispatch all due missions")
    p.add_argument("--list", action="store_true")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.tick:
        fired = tick(a.registry)
        print("dispatched {} mission(s): {}".format(len(fired), ", ".join(r for _, r in fired) or "none"))
        return 0
    if a.list:
        now = datetime.now()
        for m in load_registry(a.registry):
            print("{}  {}  {}  last_run={}  next={}".format(
                m.get("id"), m.get("name"), m.get("schedule"), m.get("last_run"),
                next_due(m, now, _parse(m.get("last_run")))))
        state = load_declarative_state()
        for m in load_declarative():
            last = state.get(m["id"])
            print("{}  {}  {}  [nix]  last_run={}  next={}".format(
                m.get("id"), m.get("name"), m.get("schedule"), last,
                next_due(m, now, _parse(last))))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
