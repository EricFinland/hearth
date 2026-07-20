#!/usr/bin/env python3
"""hearth askdb: ask the local audit database questions in plain English.

A user asks a question ("what did the demo agent do yesterday?", "how many
tokens today?"); a local model translates it into ONE read-only SQL SELECT;
this module validates the SQL is safe, runs it against the audit db in
read-only mode, and returns the rows plus a short natural-language summary.
Everything stays local: the model call is injected (in production a function
that calls the local Ollama chat), so no server is imported here.

Safety is the whole point. The model's SQL can never write, attach, run a
PRAGMA, or chain a second statement: validate_sql rejects on any doubt and the
connection is opened read-only with PRAGMA query_only=ON. Standard library only.

Config:
  HEARTH_DB           audit db path (default /var/lib/hearth/runs/audit.db)
  HEARTH_OLLAMA_URL   local Ollama base url (default http://127.0.0.1:11434)
  HEARTH_ASK_MODEL    model tag for the CLI (default llama3.2:3b)
"""

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request

DEFAULT_DB = "/var/lib/hearth/runs/audit.db"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "llama3.2:3b"

DEFAULT_LIMIT = 200
HTTP_TIMEOUT = 120

# The only tables the model is allowed to read.
SAFE_TABLES = ("agent_runs", "run_steps", "egress_log", "tripwires")

# Tokens that must never appear (as whole words) in a query. Anything that could
# write, change schema, attach another database, or run a pragma is forbidden.
FORBIDDEN = ("INSERT", "UPDATE", "DELETE", "DROP", "ALTER", "CREATE",
             "REPLACE", "ATTACH", "DETACH", "PRAGMA", "VACUUM")

# Compact schema description fed to the model so it writes correct column names.
SCHEMA = (
    "agent_runs(id, agent_name, run_id, started_at, finished_at, tokens_in, "
    "tokens_out, cost_usd, latency_ms, error, model)\n"
    "run_steps(id, agent_id, ts, seq, kind, tool, args, output, duration_ms, "
    "verdict)\n"
    "egress_log(id, agent_id, ts, tool, host, url, allowed)\n"
    "tripwires(id, agent_id, ts, tool, path, token, detail)\n"
    "Notes: run_steps.agent_id, egress_log.agent_id and tripwires.agent_id "
    "reference agent_runs.id. Timestamps are ISO-8601 text (UTC), so "
    "substr(started_at,1,10) is the date."
)


def _strip_sql_comments(sql):
    """Remove -- line comments and /* */ block comments so validation sees the
    real statement, not a keyword hidden behind a comment."""
    sql = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    sql = re.sub(r"--[^\n]*", " ", sql)
    return sql


def strip_fences(text):
    """Strip accidental markdown code fences / stray backticks the model may wrap
    around its SQL, and trailing prose after the statement. Returns a bare SQL
    string (still to be validated)."""
    t = (text or "").strip()
    # Pull the body out of a fenced block if one is present.
    m = re.search(r"```(?:sql)?\s*(.*?)```", t, flags=re.S | re.I)
    if m:
        t = m.group(1).strip()
    t = t.strip("`").strip()
    return t


def validate_sql(sql):
    """Return (ok, reason). A guard, not a parser: conservative, rejects on doubt.

    Accepts only when the query, after stripping comments and whitespace:
      * is a single statement (no inner semicolon; one optional trailing ; ok),
      * begins with SELECT (case-insensitive),
      * contains none of the FORBIDDEN tokens as whole words,
      * references only tables in SAFE_TABLES (naive: any identifier that
        follows a FROM or JOIN keyword must be a safe table name).
    """
    if not sql or not sql.strip():
        return False, "empty query"

    body = _strip_sql_comments(sql).strip()
    if not body:
        return False, "empty query"

    # A single optional trailing semicolon is allowed; strip exactly one.
    if body.endswith(";"):
        body = body[:-1].rstrip()
    # Any remaining semicolon means multiple statements.
    if ";" in body:
        return False, "multiple statements are not allowed"
    if not body:
        return False, "empty query"

    # Must start with SELECT.
    if not re.match(r"(?is)^select\b", body):
        return False, "only SELECT queries are allowed"

    upper = body.upper()
    for tok in FORBIDDEN:
        if re.search(r"\b" + tok + r"\b", upper):
            return False, "forbidden keyword: {}".format(tok)

    # Every identifier following FROM or JOIN must be a known safe table.
    refs = re.findall(r"(?is)\b(?:from|join)\s+([^\s,;()]+)", body)
    if not refs:
        return False, "no table referenced"
    for raw in refs:
        name = raw.strip().strip('"').strip("'").strip("`").strip("[]")
        # Drop a schema qualifier (db.table) if present.
        if "." in name:
            name = name.split(".")[-1]
        if name.lower() not in SAFE_TABLES:
            return False, "table not allowed: {}".format(raw)

    return True, "ok"


def _has_limit(sql):
    return re.search(r"(?is)\blimit\b", sql) is not None


def run_query(db, sql, limit=DEFAULT_LIMIT):
    """Run a validated SELECT against `db` in READ-ONLY mode and return
    {"columns": [...], "rows": [[...]], "truncated": bool}. If the query has no
    LIMIT, one is appended so a runaway result can't flood the caller.

    The connection is opened with mode=ro and PRAGMA query_only=ON, so even a
    query that slipped past validation cannot write. Best-effort: any DB or SQL
    error returns {"error": "..."}.
    """
    ok, reason = validate_sql(sql)
    if not ok:
        return {"error": reason}

    body = sql.strip()
    if body.endswith(";"):
        body = body[:-1].rstrip()

    truncated = False
    if not _has_limit(body):
        body = "{} LIMIT {}".format(body, limit + 1)
        truncated = None  # decide after we count rows

    try:
        uri = "file:{}?mode=ro".format(db)
        con = sqlite3.connect(uri, uri=True, timeout=10)
        try:
            con.execute("PRAGMA query_only=ON")
            cur = con.execute(body)
            columns = [c[0] for c in (cur.description or [])]
            fetched = cur.fetchall()
        finally:
            con.close()
    except sqlite3.Error as exc:  # noqa: BLE001
        return {"error": str(exc)}
    except OSError as exc:  # noqa: BLE001
        return {"error": str(exc)}

    if truncated is None:
        # We asked for limit+1 rows to detect truncation.
        if len(fetched) > limit:
            fetched = fetched[:limit]
            truncated = True
        else:
            truncated = False

    rows = [list(r) for r in fetched]
    return {"columns": columns, "rows": rows, "truncated": bool(truncated)}


def build_sql_prompt(question, schema):
    """Messages asking the model to emit ONLY one SQLite SELECT for `question`.
    No prose, no code fence: just the statement."""
    system = (
        "You translate a question into exactly one read-only SQLite SELECT "
        "statement over the audit database. Output ONLY the SQL: no prose, no "
        "explanation, no markdown code fence, no trailing semicolon. Use only "
        "these tables and columns:\n{schema}\n"
        "Rules: a single SELECT only; never write, delete, or alter anything; "
        "reference only the tables listed above; add a reasonable LIMIT."
    ).format(schema=schema)
    user = "Question: {}\nSQL:".format(question)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def build_answer_prompt(question, columns, rows):
    """Messages asking the model to summarize the result rows in one or two
    plain sentences that answer `question`."""
    preview = json.dumps({"columns": columns, "rows": rows[:50]},
                         default=str)[:4000]
    system = (
        "You answer a question using the result of a database query. Reply with "
        "one or two plain sentences. State the numbers from the rows; do not "
        "invent data. If there are no rows, say so plainly."
    )
    user = "Question: {}\nResult rows (JSON): {}\nAnswer:".format(
        question, preview)
    return [{"role": "system", "content": system},
            {"role": "user", "content": user}]


def ask(question, db=None, chat_fn=None):
    """Answer `question` end to end and never raise.

    chat_fn(messages) -> text is injected (the local model call). Steps: ask the
    model for SQL, strip fences, validate; if invalid return
    {"ok": False, "error": reason, "sql": sql}. Otherwise run it read-only and
    ask the model to summarize, returning {"ok": True, "question", "sql",
    "columns", "rows", "summary"}. Any failure comes back as {"ok": False, ...}.
    """
    db = db or os.environ.get("HEARTH_DB", DEFAULT_DB)
    if chat_fn is None:
        return {"ok": False, "error": "no chat function provided",
                "sql": None}

    try:
        raw = chat_fn(build_sql_prompt(question, SCHEMA))
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": "model call failed: {}".format(exc),
                "sql": None}

    sql = strip_fences(raw)
    ok, reason = validate_sql(sql)
    if not ok:
        return {"ok": False, "error": reason, "sql": sql}

    result = run_query(db, sql)
    if "error" in result:
        return {"ok": False, "error": result["error"], "sql": sql}

    columns = result["columns"]
    rows = result["rows"]

    try:
        summary = chat_fn(build_answer_prompt(question, columns, rows))
        summary = (summary or "").strip()
    except Exception as exc:  # noqa: BLE001
        summary = ""

    out = {"ok": True, "question": question, "sql": sql,
           "columns": columns, "rows": rows, "summary": summary}
    if result.get("truncated"):
        out["truncated"] = True
    return out


def _ollama_chat_fn(base_url, model):
    """Build a chat_fn that calls a local Ollama /api/chat and returns the reply
    text. Used only by the CLI; the library core stays network-free."""
    url = base_url.rstrip("/") + "/api/chat"

    def chat_fn(messages):
        payload = json.dumps({
            "model": model,
            "messages": messages,
            "stream": False,
            "options": {"temperature": 0},
        }).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8", "replace"))
        return (data.get("message") or {}).get("content", "")

    return chat_fn


def _render_table(columns, rows):
    """A minimal fixed-width text table for the CLI."""
    if not columns:
        return "(no columns)"
    widths = [len(str(c)) for c in columns]
    for r in rows:
        for i, v in enumerate(r):
            widths[i] = max(widths[i], len(str(v)))
    line = " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(columns))
    sep = "-+-".join("-" * widths[i] for i in range(len(columns)))
    out = [line, sep]
    for r in rows:
        out.append(" | ".join(
            str(v).ljust(widths[i]) for i, v in enumerate(r)))
    if not rows:
        out.append("(no rows)")
    return "\n".join(out)


def _self_test():
    import tempfile

    # 1. validate_sql: a plain SELECT over agent_runs is accepted.
    ok, reason = validate_sql("SELECT agent_name, tokens_in FROM agent_runs")
    assert ok, reason

    # LIMIT is appended by the runner when none is present.
    seeded_body = "SELECT * FROM agent_runs"
    assert not _has_limit(seeded_body)

    # Reject writes / schema changes / attach / pragma.
    for bad in ("INSERT INTO agent_runs VALUES (1)",
                "UPDATE agent_runs SET error='x'",
                "DELETE FROM agent_runs",
                "DROP TABLE agent_runs",
                "ATTACH DATABASE 'x.db' AS y",
                "PRAGMA table_info(agent_runs)"):
        ok, reason = validate_sql(bad)
        assert not ok, "should reject: {}".format(bad)

    # Reject multi-statement (two SELECTs joined by a semicolon).
    ok, reason = validate_sql(
        "SELECT 1 FROM agent_runs; SELECT 2 FROM agent_runs")
    assert not ok, "multi-statement should be rejected"

    # A single trailing semicolon is fine.
    ok, reason = validate_sql("SELECT id FROM agent_runs;")
    assert ok, reason

    # Reject unknown tables (real sqlite_master and a made-up one).
    ok, reason = validate_sql("SELECT name FROM sqlite_master")
    assert not ok, "sqlite_master should be rejected"
    ok, reason = validate_sql("SELECT * FROM secrets")
    assert not ok, "unknown table should be rejected"

    # A comment must not smuggle a forbidden keyword or a second statement.
    ok, reason = validate_sql("SELECT id FROM agent_runs -- ; DROP TABLE x")
    assert ok, reason
    ok, reason = validate_sql("/* DELETE */ SELECT id FROM agent_runs")
    assert ok, reason

    # 2. run_query against a temp db seeded with agent_runs rows.
    d = tempfile.mkdtemp(prefix="hearth-askdb-")
    db = os.path.join(d, "audit.db")
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE agent_runs (id INTEGER PRIMARY KEY, agent_name TEXT, "
        "run_id TEXT, started_at TEXT, finished_at TEXT, tokens_in INTEGER, "
        "tokens_out INTEGER, cost_usd REAL, latency_ms INTEGER, error TEXT, "
        "model TEXT)")
    con.execute(
        "INSERT INTO agent_runs (agent_name, run_id, started_at, tokens_in, "
        "tokens_out) VALUES ('demo','r1','2026-07-19T01:00:00+00:00',100,50)")
    con.execute(
        "INSERT INTO agent_runs (agent_name, run_id, started_at, tokens_in, "
        "tokens_out) VALUES ('demo','r2','2026-07-19T02:00:00+00:00',200,25)")
    con.commit()
    con.close()

    res = run_query(db, "SELECT agent_name, tokens_in FROM agent_runs "
                        "ORDER BY tokens_in")
    assert res.get("columns") == ["agent_name", "tokens_in"], res
    assert res.get("rows") == [["demo", 100], ["demo", 200]], res
    assert res.get("truncated") is False, res

    # An aggregate query works too.
    res2 = run_query(db, "SELECT SUM(tokens_in) FROM agent_runs")
    assert res2["rows"][0][0] == 300, res2

    # The read-only connection cannot write: an INSERT via run_query is both
    # rejected by validation AND would fail at the DB. Prove the DB itself is
    # read-only by attempting a raw write on a ro connection.
    write_failed = False
    try:
        uri = "file:{}?mode=ro".format(db)
        roc = sqlite3.connect(uri, uri=True)
        roc.execute("PRAGMA query_only=ON")
        try:
            roc.execute(
                "INSERT INTO agent_runs (agent_name) VALUES ('x')")
            roc.commit()
        finally:
            roc.close()
    except sqlite3.Error:
        write_failed = True
    assert write_failed, "read-only connection must reject writes"

    # 3. ask(...) with a fake chat_fn: first call returns SQL, second a summary.
    calls = {"n": 0}

    def good_chat(messages):
        calls["n"] += 1
        if calls["n"] == 1:
            return "```sql\nSELECT agent_name, tokens_in FROM agent_runs\n```"
        return "The demo agent used tokens across two runs."

    out = ask("what did the demo agent do?", db=db, chat_fn=good_chat)
    assert out["ok"] is True, out
    assert out["rows"] == [["demo", 100], ["demo", 200]], out
    assert out["sql"] == "SELECT agent_name, tokens_in FROM agent_runs", out
    assert out["summary"], out

    # ask(...) with a chat_fn that returns a destructive statement: ok=False,
    # and the db is never touched (no write happened).
    def evil_chat(messages):
        return "DROP TABLE agent_runs"

    bad = ask("delete everything", db=db, chat_fn=evil_chat)
    assert bad["ok"] is False, bad
    assert "sql" in bad and bad["sql"] == "DROP TABLE agent_runs", bad
    # The table still has exactly the two seeded rows.
    con = sqlite3.connect(db)
    n = con.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    con.close()
    assert n == 2, "db must be untouched, found {} rows".format(n)

    print("hearth-askdb self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-askdb")
    p.add_argument("question", nargs="?", help="a question in plain English")
    p.add_argument("--db", default=os.environ.get("HEARTH_DB", DEFAULT_DB))
    p.add_argument("--ollama-url",
                   default=os.environ.get("HEARTH_OLLAMA_URL",
                                          DEFAULT_OLLAMA_URL))
    p.add_argument("--model",
                   default=os.environ.get("HEARTH_ASK_MODEL", DEFAULT_MODEL))
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)

    if a.self_test:
        return _self_test()
    if not a.question:
        p.error("nothing to do (pass a question or use --self-test)")

    chat_fn = _ollama_chat_fn(a.ollama_url, a.model)
    out = ask(a.question, db=a.db, chat_fn=chat_fn)
    if not out.get("ok"):
        print("error: {}".format(out.get("error")))
        if out.get("sql"):
            print("sql: {}".format(out["sql"]))
        return 1

    print("SQL: {}".format(out["sql"]))
    print()
    print(_render_table(out["columns"], out["rows"]))
    if out.get("truncated"):
        print("(results truncated)")
    print()
    print(out["summary"] or "(no summary)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
