#!/usr/bin/env python3
"""hearth projects: make an agent project-aware.

Point it at a directory and it ingests the text/code files into the knowledge
base, each under a `name/relpath` source, so an agent can then kb_search the
project and answer grounded in the real code. Binaries, build dirs, and oversized
files are skipped. Builds on hearth_knowledge. Standard library only.
"""

import argparse
import fnmatch
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hearth_knowledge  # noqa: E402

DEFAULT_DB = hearth_knowledge.DEFAULT_DB
SKIP_DIRS = {".git", "__pycache__", "node_modules", "result", ".direnv",
             ".mypy_cache", "dist", "build", ".venv", "venv", ".cache"}
# Text/code file types worth indexing by default.
DEFAULT_GLOBS = ["*.py", "*.md", "*.txt", "*.rst", "*.nix", "*.js", "*.ts", "*.jsx",
                 "*.tsx", "*.json", "*.yaml", "*.yml", "*.toml", "*.ini", "*.cfg",
                 "*.sh", "*.html", "*.css", "*.rs", "*.go", "*.c", "*.h", "*.cpp",
                 "*.java", "*.rb", "*.lua", "*.sql"]
MAX_FILES = 300
MAX_BYTES = 1_000_000


def index_dir(db, name, root, embed_fn=None, globs=None, max_files=MAX_FILES, max_bytes=MAX_BYTES):
    """Ingest the text files under `root` into the KB under `name/relpath`.
    Returns {files, chunks, skipped, truncated}."""
    globs = globs or DEFAULT_GLOBS
    files = chunks = skipped = 0
    truncated = False
    root = os.path.realpath(root)
    if not os.path.isdir(root):
        return {"files": 0, "chunks": 0, "skipped": 0, "truncated": False, "error": "not a directory"}
    for dirpath, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS)
        for fn in sorted(names):
            if not any(fnmatch.fnmatch(fn, g) for g in globs):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > max_bytes:
                    skipped += 1
                    continue
                with open(fp, encoding="utf-8") as fh:
                    text = fh.read()
            except (OSError, UnicodeDecodeError):
                skipped += 1  # unreadable or binary
                continue
            if not text.strip():
                continue
            rel = os.path.relpath(fp, root).replace(os.sep, "/")
            chunks += hearth_knowledge.ingest(db, "{}/{}".format(name, rel), text, embed_fn=embed_fn)
            files += 1
            if files >= max_files:
                truncated = True
                return {"files": files, "chunks": chunks, "skipped": skipped, "truncated": truncated}
    return {"files": files, "chunks": chunks, "skipped": skipped, "truncated": truncated}


def project_sources(db, name):
    """List the indexed sources belonging to a project (by `name/` prefix)."""
    return [s for s in hearth_knowledge.sources(db) if s["source"].startswith(name + "/")]


def _self_test():
    import tempfile
    d = tempfile.mkdtemp(prefix="hearth-proj-")
    root = os.path.join(d, "proj")
    os.makedirs(os.path.join(root, "src"))
    os.makedirs(os.path.join(root, ".git"))  # must be skipped
    with open(os.path.join(root, "README.md"), "w") as fh:
        fh.write("This project does reproducible NixOS builds from a flake.")
    with open(os.path.join(root, "src", "app.py"), "w") as fh:
        fh.write("def main():\n    print('hello from the indexer')\n")
    with open(os.path.join(root, "src", "data.bin"), "wb") as fh:
        fh.write(bytes(range(256)) * 10)  # binary, wrong ext -> not matched anyway
    with open(os.path.join(root, ".git", "config"), "w") as fh:
        fh.write("[core] secret = do-not-index")

    db = os.path.join(d, "kb.db")
    res = index_dir(db, "myproj", root)
    assert res["files"] == 2, ("README.md + app.py indexed, .git skipped, .bin not matched", res)
    assert res["chunks"] >= 2, res
    srcs = {s["source"] for s in project_sources(db, "myproj")}
    assert "myproj/README.md" in srcs and "myproj/src/app.py" in srcs, srcs
    assert not any(".git" in s for s in srcs), ("git dir excluded", srcs)
    # the indexed content is searchable
    hits = hearth_knowledge.search(db, "reproducible flake builds")
    assert hits and hits[0]["source"] == "myproj/README.md", hits
    code = hearth_knowledge.search(db, "indexer hello main")
    assert any(h["source"] == "myproj/src/app.py" for h in code), code
    # max_files cap is honored
    big = os.path.join(d, "big")
    os.makedirs(big)
    for i in range(10):
        with open(os.path.join(big, "f{}.txt".format(i)), "w") as fh:
            fh.write("file number {}".format(i))
    capped = index_dir(db, "big", big, max_files=3)
    assert capped["files"] == 3 and capped["truncated"], capped

    print("hearth-project self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-project")
    p.add_argument("--db", default=DEFAULT_DB)
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    pi = sub.add_parser("index")
    pi.add_argument("name")
    pi.add_argument("path")
    pl = sub.add_parser("list")
    pl.add_argument("name")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.cmd == "index":
        res = index_dir(a.db, a.name, a.path, embed_fn=hearth_knowledge.make_embedder())
        print("indexed {files} files, {chunks} chunks ({skipped} skipped)".format(**res))
        return 0
    if a.cmd == "list":
        for s in project_sources(a.db, a.name):
            print("{}  ({} chunks)".format(s["source"], s["chunks"]))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
