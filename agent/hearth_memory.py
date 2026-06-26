#!/usr/bin/env python3
"""hearth memory: a self-learning store. Agents record what worked (and what
failed) and recall relevant lessons on later runs, so hearth improves over time
instead of repeating mistakes. Stored in the shared audit SQLite database.
Standard library only.
"""

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS learnings (
  id      INTEGER PRIMARY KEY AUTOINCREMENT,
  ts      TEXT NOT NULL,
  kind    TEXT,
  topic   TEXT,
  insight TEXT NOT NULL,
  tags    TEXT,
  source  TEXT
);
"""


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _con(db):
    parent = os.path.dirname(db)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(db, timeout=10)
    con.executescript(SCHEMA)
    return con


def remember(db, insight, kind="lesson", topic="", tags="", source="agent"):
    """Record a lesson. kind is e.g. 'lesson' | 'success' | 'failure' | 'pattern'.
    Returns the new row id, or None on error."""
    if not (insight or "").strip():
        return None
    try:
        con = _con(db)
        try:
            cur = con.execute(
                "INSERT INTO learnings (ts, kind, topic, insight, tags, source) VALUES (?,?,?,?,?,?)",
                (_now_iso(), kind, (topic or "")[:200], insight[:4000], (tags or "")[:300], source))
            con.commit()
            return cur.lastrowid
        finally:
            con.close()
    except sqlite3.Error:
        return None


def recall(db, query="", limit=8):
    """Return up to `limit` lessons most relevant to `query` (simple keyword
    match over topic/insight/tags), newest first. Empty query returns the most
    recent lessons."""
    if not os.path.exists(db):
        return []
    try:
        con = _con(db)
        try:
            terms = [t for t in (query or "").lower().split() if len(t) > 2]
            rows = con.execute(
                "SELECT id, ts, kind, topic, insight, tags FROM learnings ORDER BY id DESC LIMIT 500").fetchall()
        finally:
            con.close()
    except sqlite3.Error:
        return []
    items = [{"id": r[0], "ts": r[1], "kind": r[2], "topic": r[3] or "",
              "insight": r[4], "tags": r[5] or ""} for r in rows]
    if not terms:
        return items[:limit]
    def score(it):
        hay = (it["topic"] + " " + it["insight"] + " " + it["tags"]).lower()
        return sum(1 for t in terms if t in hay)
    scored = [(score(it), it) for it in items]
    scored = [(s, it) for s, it in scored if s > 0]
    scored.sort(key=lambda si: (-si[0], -si[1]["id"]))
    return [it for _, it in scored[:limit]]


def as_context(lessons):
    """Render recalled lessons as a short text block for an agent's prompt."""
    if not lessons:
        return ""
    lines = ["Relevant lessons from past hearth runs:"]
    for it in lessons:
        lines.append("- ({}) {}".format(it["kind"] or "lesson", it["insight"]))
    return "\n".join(lines)


def _self_test():
    import tempfile
    db = os.path.join(tempfile.mkdtemp(prefix="hearth-mem-"), "a.db")
    assert recall(db) == [], "empty db -> no lessons"
    rid = remember(db, "qwen2.5-coder emits tool calls as text; the loop parses them", kind="pattern", topic="tool calls", tags="ollama tools")
    assert rid, rid
    remember(db, "nix flake check fails if a module option is misspelled", kind="failure", topic="nix", tags="nix flake")
    remember(db, "unrelated note about cats", kind="lesson", topic="misc")
    hits = recall(db, "nix flake check")
    assert hits and "nix flake check" in hits[0]["insight"], hits
    assert all("cat" not in h["insight"] for h in hits), ("query should not match the cat note", hits)
    assert len(recall(db)) == 3, "empty query returns recent"
    assert remember(db, "   ") is None, "blank insight ignored"
    ctx = as_context(recall(db, "nix"))
    assert "lessons" in ctx.lower() and "nix" in ctx.lower(), ctx
    print("hearth-memory self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-memory")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=False)
    pr = sub.add_parser("remember")
    pr.add_argument("insight")
    pr.add_argument("--kind", default="lesson")
    pr.add_argument("--tags", default="")
    pc = sub.add_parser("recall")
    pc.add_argument("query", nargs="?", default="")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.cmd == "remember":
        print(remember(a.db, a.insight, kind=a.kind, tags=a.tags))
        return 0
    if a.cmd == "recall":
        for it in recall(a.db, a.query):
            print("[{}] {}".format(it["kind"], it["insight"]))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
