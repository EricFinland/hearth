#!/usr/bin/env python3
"""hearth budget: the daily token spend circuit breaker.

Reads the audit db (agent_runs) to total today's token spend, compares it to a
daily cap from the environment, and answers one question: is this hearth over
budget right now? The loop asks before every model call and refuses to burn
more tokens once the cap is reached. Pure and injectable (you pass the db path
and `now`), so it is fully testable with no clock and no real audit db.
Standard library only.

Config:
  HEARTH_DAILY_TOKEN_CAP   max tokens (in+out) per UTC day; 0/unset = off
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"


def cap():
    """The daily token cap from HEARTH_DAILY_TOKEN_CAP. Unset, zero, negative,
    or junk all mean 0 (the breaker is off)."""
    raw = os.environ.get("HEARTH_DAILY_TOKEN_CAP", "")
    try:
        n = int(raw)
    except (TypeError, ValueError):
        return 0
    return n if n > 0 else 0


def used_today(db, now=None):
    """Total tokens (in+out) and run count for the current UTC day, summed from
    agent_runs rows whose started_at falls on the same UTC date as `now`.
    Best-effort: a missing db or table reads as zero spend."""
    now = now or datetime.now(timezone.utc)
    day = now.strftime("%Y-%m-%d")
    try:
        con = sqlite3.connect(db, timeout=10)
        try:
            row = con.execute(
                "SELECT COALESCE(SUM(COALESCE(tokens_in,0)+COALESCE(tokens_out,0)),0), "
                "COUNT(*) FROM agent_runs WHERE substr(started_at,1,10)=?",
                (day,)).fetchone()
        finally:
            con.close()
        return {"tokens": int(row[0] or 0), "runs": int(row[1] or 0)}
    except sqlite3.Error:
        return {"tokens": 0, "runs": 0}


def check(db, now=None):
    """One-call budget status: {"cap", "tokens", "runs", "remaining", "capped"}.
    capped is True only when a cap is set and today's spend has reached it
    (spend equal to the cap counts as capped)."""
    c = cap()
    used = used_today(db, now=now)
    n = used["tokens"]
    return {"cap": c, "tokens": n, "runs": used["runs"],
            "remaining": max(0, c - n), "capped": c > 0 and n >= c}


def _self_test():
    import tempfile
    d = tempfile.mkdtemp(prefix="hearth-budget-")
    db = os.path.join(d, "audit.db")
    now = datetime(2026, 7, 19, 12, 0, 0, tzinfo=timezone.utc)

    saved = os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
    try:
        # missing db / table: zero spend, no crash
        assert used_today(db, now=now) == {"tokens": 0, "runs": 0}, "missing db -> zeros"

        con = sqlite3.connect(db)
        con.execute(
            "CREATE TABLE agent_runs (id INTEGER PRIMARY KEY, agent_name TEXT, "
            "run_id TEXT, started_at TEXT, finished_at TEXT, tokens_in INTEGER, "
            "tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER, error TEXT, "
            "model TEXT)")
        rows = [
            ("a", "r1", "2026-07-19T01:00:00+00:00", 100, 50),    # today
            ("a", "r2", "2026-07-19T11:59:00+00:00", 200, 0),     # today
            ("b", "r3", "2026-07-18T23:59:00+00:00", 900, 900),   # yesterday
        ]
        for name, rid, ts, tin, tout in rows:
            con.execute(
                "INSERT INTO agent_runs (agent_name, run_id, started_at, finished_at, "
                "tokens_in, tokens_out, cost_usd, latency_ms, error, model) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (name, rid, ts, ts, tin, tout, 0.0, 1, None, "mock"))
        con.commit()
        con.close()

        # only today's rows count
        assert used_today(db, now=now) == {"tokens": 350, "runs": 2}, used_today(db, now=now)

        # cap(): unset / zero / junk / negative all read as off
        assert cap() == 0, "unset -> 0"
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "0"
        assert cap() == 0, "zero -> 0"
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "banana"
        assert cap() == 0, "junk -> 0"
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "-5"
        assert cap() == 0, "negative -> 0"
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "1000"
        assert cap() == 1000

        # under the cap: not capped, remaining is what is left
        c = check(db, now=now)
        assert c == {"cap": 1000, "tokens": 350, "runs": 2,
                     "remaining": 650, "capped": False}, c
        # boundary: spend equal to the cap IS capped
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "350"
        c2 = check(db, now=now)
        assert c2["capped"] is True and c2["remaining"] == 0, c2
        # one token of headroom left: not capped yet
        os.environ["HEARTH_DAILY_TOKEN_CAP"] = "351"
        assert check(db, now=now)["capped"] is False
        # cap off: never capped, even with spend on the books
        os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
        c3 = check(db, now=now)
        assert c3["capped"] is False and c3["cap"] == 0 and c3["tokens"] == 350, c3
    finally:
        if saved is None:
            os.environ.pop("HEARTH_DAILY_TOKEN_CAP", None)
        else:
            os.environ["HEARTH_DAILY_TOKEN_CAP"] = saved

    print("hearth-budget self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-budget")
    p.add_argument("--db", default=os.environ.get("HEARTH_DB", DEFAULT_DB))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    st = check(a.db)
    print("cap={cap} tokens={tokens} runs={runs} remaining={remaining} capped={capped}".format(**st))
    return 0


if __name__ == "__main__":
    sys.exit(main())
