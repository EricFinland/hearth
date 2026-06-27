#!/usr/bin/env python3
"""hearth doctor: a one-command health check of a hearth install.

Runs a set of probes (Ollama reachable, audit DB writable, key services active,
disk space) and prints a pass/warn/fail checklist, exiting non-zero if anything
failed. The probe set is injectable, so the aggregation and reporting are fully
testable with no real system. Standard library only.
"""

import argparse
import os
import shutil
import subprocess
import sys
import urllib.request

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
DEFAULT_OLLAMA = os.environ.get("HEARTH_OLLAMA", "http://127.0.0.1:11434")
SYSTEMCTL = shutil.which("systemctl") or "/run/current-system/sw/bin/systemctl"
_SYMBOL = {"ok": "OK  ", "warn": "WARN", "fail": "FAIL"}
_RANK = {"ok": 0, "warn": 1, "fail": 2}


def check_ollama(url=DEFAULT_OLLAMA):
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=8) as resp:
            import json
            n = len(json.loads(resp.read().decode()).get("models", []))
        return "ok", "reachable, {} model(s) pulled".format(n)
    except Exception as exc:  # noqa: BLE001
        return "fail", "not reachable at {} ({})".format(url, type(exc).__name__)


def check_db(path=DEFAULT_DB):
    parent = os.path.dirname(path)
    if os.path.exists(path):
        return ("ok", "present and writable: " + path) if os.access(path, os.W_OK) \
            else ("fail", "present but NOT writable: " + path)
    if parent and os.path.isdir(parent) and os.access(parent, os.W_OK):
        return "warn", "not created yet, but the directory is writable"
    return "fail", "missing and the directory is not writable: " + str(parent)


def check_disk(path="/var/lib/hearth", min_gb=2.0):
    target = path if os.path.exists(path) else os.path.dirname(path) or "/"
    try:
        free_gb = shutil.disk_usage(target).free / 1e9
    except OSError as exc:
        return "warn", "could not check disk ({})".format(exc)
    if free_gb < min_gb:
        return "fail", "only {:.1f} GB free".format(free_gb)
    return "ok", "{:.1f} GB free".format(free_gb)


def check_unit(unit):
    try:
        r = subprocess.run([SYSTEMCTL, "is-active", unit], capture_output=True, text=True, timeout=6)
        s = (r.stdout or "").strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return "warn", "could not query ({})".format(exc)
    return ("ok", "active") if s == "active" else ("warn", s or "inactive")


def default_probes():
    return [
        ("ollama", check_ollama),
        ("audit database", check_db),
        ("disk space", check_disk),
        ("mapd service", lambda: check_unit("hearth-mapd.service")),
        ("growth daemon", lambda: check_unit("hearth-grow.service")),
        ("scheduler timer", lambda: check_unit("hearth-schedule.timer")),
    ]


def run_checks(probes=None):
    """Run probes [(name, fn)] -> [{name, status, detail}]. A probe that raises
    is reported as a fail rather than crashing the doctor."""
    results = []
    for name, fn in (probes or default_probes()):
        try:
            status, detail = fn()
        except Exception as exc:  # noqa: BLE001
            status, detail = "fail", "probe error: {}".format(exc)
        if status not in _RANK:
            status = "warn"
        results.append({"name": name, "status": status, "detail": detail})
    return results


def overall(results):
    worst = max((_RANK[r["status"]] for r in results), default=0)
    return {0: "ok", 1: "warn", 2: "fail"}[worst]


def format_report(results):
    lines = ["hearth doctor"]
    for r in results:
        lines.append("  [{}] {}: {}".format(_SYMBOL.get(r["status"], "?"), r["name"], r["detail"]))
    lines.append("overall: " + overall(results).upper())
    return "\n".join(lines)


def _self_test():
    fake = [
        ("a", lambda: ("ok", "fine")),
        ("b", lambda: ("warn", "meh")),
        ("c", lambda: (_ for _ in ()).throw(RuntimeError("boom"))),  # raises -> fail
    ]
    res = run_checks(fake)
    assert [r["status"] for r in res] == ["ok", "warn", "fail"], res
    assert "probe error" in res[2]["detail"], res
    assert overall(res) == "fail", res
    assert overall([{"status": "ok", "name": "x", "detail": ""}]) == "ok"
    assert overall([{"status": "warn", "name": "x", "detail": ""}]) == "warn"
    rep = format_report(res)
    assert "FAIL" in rep and "overall: FAIL" in rep and "a:" in rep, rep
    # bad status from a probe is coerced to warn, not crash
    assert run_checks([("x", lambda: ("weird", "?"))])[0]["status"] == "warn"
    # the real check_db reports writable for a temp file
    import tempfile
    p = os.path.join(tempfile.mkdtemp(prefix="hearth-doc-"), "a.db")
    open(p, "w").close()
    assert check_db(p)[0] == "ok", check_db(p)
    assert check_db("/nonexistent_zzz/deep/a.db")[0] == "fail"
    print("hearth-doctor self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-doctor")
    p.add_argument("--self-test", action="store_true")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    results = run_checks()
    print(format_report(results))
    return 0 if overall(results) != "fail" else 1


if __name__ == "__main__":
    sys.exit(main())
