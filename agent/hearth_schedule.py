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

The schedule math is pure and injectable (you pass `now`), so it is fully
testable with no clock, no systemd, and no Ollama. Standard library only.
"""

import argparse
import json
import os
import subprocess
import sys
import uuid
from datetime import datetime

# Lives in an operator-owned subdir: operator (who runs the scheduler and mapd)
# can write here, but not /var/lib/hearth itself (0750 hearth).
DEFAULT_REGISTRY = os.environ.get("HEARTH_SCHEDULE", "/var/lib/hearth/scheduler/schedule.json")
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
        "prompt": mission.get("goal") or "", "mode": mission.get("mode") or "bypass",
        "creds": mission.get("creds") or "",
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


def tick(path=DEFAULT_REGISTRY, now=None, dispatch_fn=None):
    """Dispatch every due mission and stamp its last_run. Returns the list of
    (mission_id, run_id) dispatched."""
    now = now or datetime.now()
    dispatch_fn = dispatch_fn or dispatch
    missions = load_registry(path)
    fired = []
    changed = False
    for m in missions:
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
    fired_log = []
    add_mission({"id": "due1", "name": "x", "goal": "g", "schedule": {"every_minutes": 5}}, path=reg)
    out = tick(reg, now=base, dispatch_fn=lambda m: fired_log.append(m["id"]) or ("run-" + m["id"]))
    ids = [mid for mid, _ in out]
    assert "due1" in ids, ("due1 should fire", out)
    # immediately ticking again: due1 just ran, the 60-min one already fired too -> nothing new
    out2 = tick(reg, now=base, dispatch_fn=lambda m: fired_log.append(m["id"]) or "x")
    assert out2 == [], ("nothing due right after a tick", out2)
    # an hour later the 5-min mission is due again
    out3 = tick(reg, now=base + timedelta(hours=1), dispatch_fn=lambda m: "r")
    assert any(mid == "due1" for mid, _ in out3), out3

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
        for m in load_registry(a.registry):
            print("{}  {}  {}  last_run={}".format(
                m.get("id"), m.get("name"), m.get("schedule"), m.get("last_run")))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
