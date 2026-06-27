#!/usr/bin/env python3
"""hearth knowledge base: a local, private retrieval store.

Agents ingest documents (chunked) and later search them for the chunks most
relevant to a query, so a local model can ground its answers in your own files
instead of guessing. Retrieval is pure-stdlib TF-IDF cosine similarity: no
embedding model to pull, deterministic, works offline, and easy to test. The
ranking is behind a seam (`rank_fn`) so it can be swapped for Ollama embeddings
later without touching callers. Stored in the shared audit SQLite database.
Standard library only.
"""

import argparse
import math
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
CHUNK_CHARS = 800

SCHEMA = """
CREATE TABLE IF NOT EXISTS kb_chunks (
  id        INTEGER PRIMARY KEY AUTOINCREMENT,
  source    TEXT NOT NULL,
  chunk_idx INTEGER NOT NULL,
  text      TEXT NOT NULL,
  ts        TEXT NOT NULL
);
"""

_WORD = re.compile(r"[a-z0-9]+")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _con(db):
    parent = os.path.dirname(db)
    if parent:
        os.makedirs(parent, exist_ok=True)
    con = sqlite3.connect(db, timeout=10)
    con.executescript(SCHEMA)
    return con


def _tokens(text):
    return _WORD.findall((text or "").lower())


def chunk_text(text, size=CHUNK_CHARS):
    """Split text into ~size-char chunks on paragraph boundaries, hard-splitting
    any paragraph longer than size."""
    chunks = []
    cur = ""
    for para in re.split(r"\n\s*\n", text or ""):
        para = para.strip()
        if not para:
            continue
        if len(para) > size:
            if cur:
                chunks.append(cur)
                cur = ""
            for i in range(0, len(para), size):
                chunks.append(para[i:i + size])
        elif not cur:
            cur = para
        elif len(cur) + len(para) + 1 <= size:
            cur += "\n" + para
        else:
            chunks.append(cur)
            cur = para
    if cur:
        chunks.append(cur)
    return chunks


def ingest(db, source, text, size=CHUNK_CHARS):
    """Add (or replace) a document under `source`. Re-ingesting the same source
    replaces its chunks. Returns the number of chunks stored."""
    chunks = chunk_text(text, size)
    con = _con(db)
    try:
        con.execute("DELETE FROM kb_chunks WHERE source=?", (source,))
        con.executemany(
            "INSERT INTO kb_chunks (source, chunk_idx, text, ts) VALUES (?,?,?,?)",
            [(source, i, c, _now()) for i, c in enumerate(chunks)])
        con.commit()
    finally:
        con.close()
    return len(chunks)


def forget(db, source):
    """Remove a document from the knowledge base. Returns rows deleted."""
    if not os.path.exists(db):
        return 0
    con = _con(db)
    try:
        cur = con.execute("DELETE FROM kb_chunks WHERE source=?", (source,))
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def sources(db):
    """List ingested sources with their chunk counts."""
    if not os.path.exists(db):
        return []
    con = _con(db)
    try:
        rows = con.execute(
            "SELECT source, COUNT(*) FROM kb_chunks GROUP BY source ORDER BY source").fetchall()
    finally:
        con.close()
    return [{"source": r[0], "chunks": r[1]} for r in rows]


def _tfidf_rank(query, docs):
    """Rank docs (list of {text}) by TF-IDF cosine similarity to the query.
    Returns [(score, doc)] for docs with score > 0, highest first."""
    toks = [_tokens(d["text"]) for d in docs]
    n = len(docs)
    df = Counter()
    for t in toks:
        for term in set(t):
            df[term] += 1

    def idf(term):
        return math.log(1 + n / (1 + df.get(term, 0)))

    def vec(tok):
        tf = Counter(tok)
        total = len(tok) or 1
        return {term: (c / total) * idf(term) for term, c in tf.items()}

    qv = vec(_tokens(query))
    if not qv:
        return []
    qnorm = math.sqrt(sum(v * v for v in qv.values()))
    out = []
    for d, tok in zip(docs, toks):
        dv = vec(tok)
        dnorm = math.sqrt(sum(v * v for v in dv.values()))
        if not dnorm or not qnorm:
            continue
        num = sum(qv.get(term, 0) * v for term, v in dv.items())
        score = num / (dnorm * qnorm)
        if score > 0:
            out.append((score, d))
    out.sort(key=lambda x: -x[0])
    return out


def search(db, query, limit=5, rank_fn=None):
    """Return up to `limit` chunks most relevant to `query`, highest score first."""
    if not os.path.exists(db) or not (query or "").strip():
        return []
    con = _con(db)
    try:
        rows = con.execute("SELECT source, chunk_idx, text FROM kb_chunks").fetchall()
    finally:
        con.close()
    docs = [{"source": r[0], "chunk": r[1], "text": r[2]} for r in rows]
    if not docs:
        return []
    ranked = (rank_fn or _tfidf_rank)(query, docs)
    return [{"source": d["source"], "chunk": d["chunk"], "score": round(s, 4), "text": d["text"]}
            for s, d in ranked[:limit]]


def as_context(hits):
    """Render search hits as a prompt block."""
    if not hits:
        return ""
    lines = ["Relevant knowledge:"]
    for h in hits:
        lines.append("- [{} #{}] {}".format(h["source"], h["chunk"], h["text"]))
    return "\n".join(lines)


def _self_test():
    import tempfile
    db = os.path.join(tempfile.mkdtemp(prefix="hearth-kb-"), "a.db")
    assert search(db, "anything") == [], "empty db -> no hits"

    n = ingest(db, "nix.md",
               "NixOS builds the whole system from a flake.\n\n"
               "Rollback is atomic at the bootloader level.")
    assert n == 1, ("short doc -> one chunk", n)
    ingest(db, "cats.md", "Cats are small furry animals that nap a lot.")
    ingest(db, "ollama.md", "Ollama serves local language models on your own GPU.")

    hits = search(db, "how does nixos rollback work")
    assert hits and hits[0]["source"] == "nix.md", hits
    assert all("cat" not in h["text"].lower() or h["source"] != "cats.md" or i == 0
               for i, h in enumerate(hits)), hits
    # the cat doc should not outrank the nix doc for a nix query
    assert hits[0]["source"] == "nix.md" and hits[0]["score"] > 0, hits

    gpu = search(db, "local models gpu")
    assert gpu and gpu[0]["source"] == "ollama.md", gpu

    # re-ingest replaces, does not duplicate
    ingest(db, "nix.md", "Updated: nix flake check validates the configuration.")
    assert len(sources(db)) == 3, sources(db)
    nixhits = search(db, "flake check validate")
    assert nixhits[0]["source"] == "nix.md" and "validates" in nixhits[0]["text"], nixhits

    # chunking: a long doc splits into multiple chunks
    long_doc = "\n\n".join("Paragraph number {} about reproducible systems.".format(i) for i in range(60))
    cn = ingest(db, "long.md", long_doc)
    assert cn >= 2, ("long doc splits", cn)

    assert forget(db, "cats.md") >= 1 and not any(s["source"] == "cats.md" for s in sources(db))
    ctx = as_context(search(db, "ollama"))
    assert "Relevant knowledge" in ctx and "ollama.md" in ctx, ctx

    print("hearth-knowledge self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-knowledge")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    pi = sub.add_parser("ingest")
    pi.add_argument("source")
    pi.add_argument("file")
    ps = sub.add_parser("search")
    ps.add_argument("query")
    sub.add_parser("sources")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.cmd == "ingest":
        with open(a.file, errors="replace") as fh:
            print("ingested", ingest(a.db, a.source, fh.read()), "chunks")
        return 0
    if a.cmd == "search":
        for h in search(a.db, a.query):
            print("[{} #{}] {:.3f}  {}".format(h["source"], h["chunk"], h["score"], h["text"][:120]))
        return 0
    if a.cmd == "sources":
        for s in sources(a.db):
            print("{}  ({} chunks)".format(s["source"], s["chunks"]))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
