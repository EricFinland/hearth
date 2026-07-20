#!/usr/bin/env python3
"""hearth egress wall: OS-level enforcement of a run's allowed-hosts list.

The tool layer already gates outbound requests on HEARTH_ALLOWED_HOSTS, but a
shelled-out curl never asks the tool layer. This module closes that hole with
per-run nftables rules keyed on the run's cgroup (background runs execute as
hearth-agent@<id>.service). `apply` resolves the allowed hosts and loads a
ruleset that accepts loopback, DNS, and the resolved addresses, then logs and
drops everything else. `remove` tears the run's rules down. `watch` follows the
kernel log for the drop prefix and records each blocked connection to the
existing egress_log audit table (allowed=0, tool="os"), so the cockpit view
shows tool-layer and OS-layer blocks side by side.

Rule building, host resolution, nft invocation, and the log source are all
injectable, so the whole module is testable with no root, no nft binary, and no
journal. Standard library only.
"""

import argparse
import os
import re
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone

DEFAULT_DB = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
JOURNALCTL = ["journalctl", "-k", "-f", "-o", "cat"]
LOG_PREFIX = "hearth-egress"
DEDUP_WINDOW_S = 10.0

EGRESS_SCHEMA = """
CREATE TABLE IF NOT EXISTS egress_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  agent_id TEXT, ts TEXT, tool TEXT, host TEXT, url TEXT, allowed INTEGER
);
"""

_PREFIX_RE = re.compile(r"hearth-egress (\S+) ")
_DST_RE = re.compile(r"\bDST=([0-9a-fA-F:.]+)")


def sanitize_id(run_id):
    """Make a run id safe for use in an nft chain name: keep [a-zA-Z0-9_],
    replace everything else with _."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", run_id or "")


def _run_nft(args, stdin_text=None):
    """Run nft with the given args (stdin_text piped in when set). Returns
    (returncode, stdout); a missing binary or timeout reads as (1, "")."""
    try:
        p = subprocess.run(["nft"] + list(args), input=stdin_text,
                           capture_output=True, text=True, timeout=20)
        return p.returncode, p.stdout
    except (OSError, subprocess.SubprocessError):
        return 1, ""


def _default_resolve(host):
    """Resolve a hostname to its IPv4/IPv6 addresses (deduped, order kept).
    Raises on failure; the caller treats that as 'skip this host'."""
    addrs = []
    for info in socket.getaddrinfo(host, None):
        addr = info[4][0]
        if addr not in addrs:
            addrs.append(addr)
    return addrs


def default_cgroup(run_id):
    """The cgroup path nft should match for a run, relative to the cgroupv2
    root (no leading slash). systemd places a templated unit under an
    auto-generated per-template slice, so the real path has an intermediate
    component: system.slice/system-hearth\\x2dagent.slice/hearth-agent@<id>.service.
    resolve_cgroup() queries systemd for the live path; this is the fallback
    when that query is unavailable (for example under --dry-run off-target)."""
    return ("system.slice/system-hearth\\x2dagent.slice/"
            "hearth-agent@{}.service".format(run_id))


def resolve_cgroup(run_id, show_fn=None):
    """Ask systemd for the run unit's actual ControlGroup, so the nft match
    tracks whatever slice layout systemd chose rather than a hardcoded guess.
    Returns the path with the leading slash stripped, or the default when the
    unit is not known yet. show_fn is injectable for tests."""
    unit = "hearth-agent@{}.service".format(run_id)
    if show_fn is None:
        def show_fn(u):
            try:
                p = subprocess.run(
                    ["systemctl", "show", u, "-p", "ControlGroup", "--value"],
                    capture_output=True, text=True, timeout=10)
                return p.stdout.strip()
            except (OSError, subprocess.SubprocessError):
                return ""
    path = (show_fn(unit) or "").strip()
    if not path:
        return default_cgroup(run_id)
    return path.lstrip("/")


def _cgroup_level(cgroup):
    """nftables socket cgroupv2 matches an ancestor at a given level, counted
    from the cgroupv2 root (root is 0, so the first path component is 1). The
    leaf service therefore sits at a level equal to its component count."""
    return len([c for c in cgroup.split("/") if c])


def build_ruleset(run_id, addrs, cgroup=None):
    """Render the per-run nft ruleset as text for `nft -f -`.

    `add table` / `add chain` are idempotent, and only the run's own chain is
    flushed, so re-applying one run never disturbs another's rules. Traffic
    from the run's cgroup jumps to its chain: loopback, DNS, and each resolved
    address are accepted; anything else is logged (with a parseable prefix the
    `watch` subcommand knows) and dropped. cgroup is the run unit's real
    cgroupv2 path (see resolve_cgroup); it defaults to the standard layout."""
    chain = "run_" + sanitize_id(run_id)
    if cgroup is None:
        cgroup = default_cgroup(run_id)
    level = _cgroup_level(cgroup)
    lines = [
        "add table inet hearth",
        "add chain inet hearth output "
        "{ type filter hook output priority filter ; policy accept ; }",
        "add chain inet hearth {}".format(chain),
        "flush chain inet hearth {}".format(chain),
        'add rule inet hearth {} oif "lo" accept'.format(chain),
        "add rule inet hearth {} ip daddr 127.0.0.0/8 accept".format(chain),
        "add rule inet hearth {} ip6 daddr ::1 accept".format(chain),
        "add rule inet hearth {} udp dport 53 accept".format(chain),
        "add rule inet hearth {} tcp dport 53 accept".format(chain),
    ]
    for addr in addrs:
        fam = "ip6" if ":" in addr else "ip"
        lines.append("add rule inet hearth {} {} daddr {} accept".format(chain, fam, addr))
    lines.append('add rule inet hearth {} log prefix "{} {} " counter drop'.format(
        chain, LOG_PREFIX, run_id))
    lines.append('add rule inet hearth output socket cgroupv2 level {} "{}" jump {}'.format(
        level, cgroup, chain))
    return "\n".join(lines) + "\n"


def apply_rules(run_id, hosts_csv, dry_run=False, resolve_fn=None, nft_fn=None,
                cgroup_fn=None):
    """Resolve the allowed hosts and load the per-run ruleset. Empty hosts_csv
    keeps tool-layer semantics (no allowlist means allow-all): nothing is
    applied. --dry-run prints the generated ruleset instead of loading it.
    cgroup_fn resolves the run unit's real cgroupv2 path (injectable for tests
    and skipped under --dry-run, which uses the default layout)."""
    hosts = [h.strip() for h in (hosts_csv or "").split(",") if h.strip()]
    if not hosts:
        print("no allowed hosts for {}: allow-all, no rules applied".format(run_id))
        return 0
    resolve_fn = resolve_fn or _default_resolve
    addrs = []
    for host in hosts:
        try:
            got = resolve_fn(host)
        except Exception:  # noqa: BLE001 - one unresolvable host must not sink the rest
            got = []
        for addr in got:
            if addr not in addrs:
                addrs.append(addr)
    if dry_run:
        cgroup = default_cgroup(run_id)
    elif cgroup_fn is not None:
        cgroup = cgroup_fn(run_id)
    else:
        cgroup = resolve_cgroup(run_id)
    ruleset = build_ruleset(run_id, addrs, cgroup=cgroup)
    if dry_run:
        print(ruleset, end="")
        return 0
    # A re-apply must not stack a second jump rule for the same run.
    remove_rules(run_id, nft_fn=nft_fn)
    nft_fn = nft_fn or _run_nft
    rc, _out = nft_fn(["-f", "-"], stdin_text=ruleset)
    if rc != 0:
        print("nft load failed for {} (rc={})".format(run_id, rc))
        return 1
    print("egress wall up for {}: {} host(s), {} address(es)".format(
        run_id, len(hosts), len(addrs)))
    return 0


def remove_rules(run_id, nft_fn=None):
    """Delete the run's jump rule (found by handle in the output chain) and its
    per-run chain. Best-effort: exits 0 even when nothing is there."""
    nft_fn = nft_fn or _run_nft
    chain = "run_" + sanitize_id(run_id)
    rc, listing = nft_fn(["-a", "list", "chain", "inet", "hearth", "output"])
    if rc == 0 and listing:
        pat = re.compile(r"\bjump {}\b.*# handle (\d+)".format(re.escape(chain)))
        for line in listing.splitlines():
            m = pat.search(line)
            if m:
                nft_fn(["delete", "rule", "inet", "hearth", "output",
                        "handle", m.group(1)])
    nft_fn(["delete", "chain", "inet", "hearth", chain])
    return 0


def parse_log_line(line):
    """Extract (run_id, dst) from a netfilter log line carrying our prefix, or
    None when the line is not ours or has no DST."""
    m = _PREFIX_RE.search(line or "")
    if not m:
        return None
    d = _DST_RE.search(line)
    if not d:
        return None
    return m.group(1), d.group(1)


def _record_block(db_path, run_id, dst):
    """Write one blocked connection to egress_log. Best-effort: an unwritable
    db must never kill the watcher."""
    try:
        con = sqlite3.connect(db_path, timeout=10)
        try:
            con.executescript(EGRESS_SCHEMA)
            con.execute(
                "INSERT INTO egress_log (agent_id, ts, tool, host, url, allowed) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, datetime.now(timezone.utc).isoformat(), "os", dst, "", 0))
            con.commit()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        pass


def watch_lines(lines, db_path, window=DEDUP_WINDOW_S, clock=time.monotonic):
    """Consume log lines, record each blocked (run_id, dst) to egress_log.
    A burst of drops for the same (run_id, dst) within `window` seconds writes
    one row, not hundreds. Returns the number of rows written."""
    recent = {}
    written = 0
    for line in lines:
        parsed = parse_log_line(line)
        if not parsed:
            continue
        run_id, dst = parsed
        now = clock()
        last = recent.get((run_id, dst))
        if last is not None and now - last < window:
            continue
        recent[(run_id, dst)] = now
        _record_block(db_path, run_id, dst)
        written += 1
    return written


def cmd_watch(from_file, db_path, window):
    if from_file:
        try:
            with open(from_file) as fh:
                n = watch_lines(fh, db_path, window=window)
        except OSError:
            n = 0
        print("recorded {} blocked connection(s)".format(n))
        return 0
    try:
        proc = subprocess.Popen(JOURNALCTL, stdout=subprocess.PIPE, text=True)
    except OSError as exc:
        print("cannot start journalctl: {}".format(exc))
        return 1
    try:
        watch_lines(proc.stdout, db_path, window=window)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            proc.terminate()
        except OSError:
            pass
    return 0


def _self_test():
    import contextlib
    import io
    import tempfile

    # chain-name sanitizing
    assert sanitize_id("run-42.a") == "run_42_a"
    assert sanitize_id("ok_1") == "ok_1"
    assert sanitize_id("a b/c") == "a_b_c"

    # ruleset generation: mixed v4/v6, one unresolvable host skipped, dry-run
    table = {"example.com": ["93.184.216.34", "2606:2800:220:1::1"],
             "api.example.com": ["93.184.216.35"]}
    def fake_resolve(host):
        if host not in table:
            raise OSError("resolve failed")
        return table[host]
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = apply_rules("net-7.x", "example.com, api.example.com, no.such.host",
                         dry_run=True, resolve_fn=fake_resolve)
    assert rc == 0
    rs = buf.getvalue()
    assert "add table inet hearth" in rs
    assert "add chain inet hearth output " \
           "{ type filter hook output priority filter ; policy accept ; }" in rs
    assert "add chain inet hearth run_net_7_x" in rs, "sanitized chain name"
    assert "flush chain inet hearth run_net_7_x" in rs, "only own chain flushed"
    assert 'socket cgroupv2 level 3 "system.slice/system-hearth\\x2dagent.slice/' \
           'hearth-agent@net-7.x.service" jump run_net_7_x' in rs, \
           "dry-run uses the default 3-level slice path and matching level"
    assert 'run_net_7_x oif "lo" accept' in rs
    assert "run_net_7_x ip daddr 127.0.0.0/8 accept" in rs
    assert "run_net_7_x ip6 daddr ::1 accept" in rs
    assert "run_net_7_x udp dport 53 accept" in rs
    assert "run_net_7_x tcp dport 53 accept" in rs
    assert "run_net_7_x ip daddr 93.184.216.34 accept" in rs
    assert "run_net_7_x ip daddr 93.184.216.35 accept" in rs
    assert "run_net_7_x ip6 daddr 2606:2800:220:1::1 accept" in rs
    assert 'log prefix "hearth-egress net-7.x " counter drop' in rs
    assert "no.such.host" not in rs, "unresolvable host must be skipped"
    lines = [ln for ln in rs.splitlines() if ln]
    assert "counter drop" in lines[-2], "drop is the run chain's last rule"
    assert lines[-1].startswith("add rule inet hearth output "), "jump added last"

    # cgroup resolution: a live ControlGroup drives the match path and level;
    # an unknown unit falls back to the default layout.
    assert _cgroup_level("system.slice/system-hearth\\x2dagent.slice/x.service") == 3
    assert _cgroup_level("/a/b/") == 2
    resolved = resolve_cgroup(
        "run9",
        show_fn=lambda u: "/system.slice/system-hearth\\x2dagent.slice/"
                          "hearth-agent@run9.service")
    assert resolved.startswith("system.slice/"), "leading slash stripped"
    assert resolve_cgroup("run9", show_fn=lambda u: "") == default_cgroup("run9")
    buf_cg = io.StringIO()
    with contextlib.redirect_stdout(buf_cg):
        apply_rules("run9", "example.com", dry_run=True, resolve_fn=fake_resolve)
    # a real (deeper) cgroup injected at apply time flows into the rule level
    rs_cg = build_ruleset("run9", ["1.2.3.4"],
                          cgroup="a/b/c/d/hearth-agent@run9.service")
    assert 'socket cgroupv2 level 5 "a/b/c/d/hearth-agent@run9.service"' in rs_cg

    # empty hosts: allow-all, nft never invoked
    calls = []
    def spy_nft(args, stdin_text=None):
        calls.append(list(args))
        return 0, ""
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        rc2 = apply_rules("net-8", "  ", dry_run=False,
                          resolve_fn=fake_resolve, nft_fn=spy_nft)
    assert rc2 == 0 and "allow-all" in buf2.getvalue()
    assert calls == [], "empty hosts must not touch nft"

    # remove: parses the jump handle, deletes rule + chain, spares other runs
    listing = (
        "table inet hearth {\n"
        "  chain output { # handle 1\n"
        '    socket cgroupv2 level 2 "system.slice/hearth-agent@net-7.x.service"'
        " jump run_net_7_x # handle 12\n"
        '    socket cgroupv2 level 2 "system.slice/hearth-agent@other.service"'
        " jump run_other # handle 13\n"
        "  }\n}\n")
    rcalls = []
    def fake_nft(args, stdin_text=None):
        rcalls.append(list(args))
        if list(args[:2]) == ["-a", "list"]:
            return 0, listing
        return 0, ""
    assert remove_rules("net-7.x", nft_fn=fake_nft) == 0
    assert ["delete", "rule", "inet", "hearth", "output", "handle", "12"] in rcalls
    assert ["delete", "chain", "inet", "hearth", "run_net_7_x"] in rcalls
    assert not any(c[:2] == ["delete", "rule"] and c[-1] == "13" for c in rcalls), \
        "other runs' jump rules must be untouched"
    # remove with no table at all is still a clean 0
    assert remove_rules("ghost", nft_fn=lambda a, stdin_text=None: (1, "")) == 0

    # watch: line parsing
    good = ("hearth-egress net-7.x IN= OUT=eth0 SRC=10.0.0.9 "
            "DST=93.184.216.34 LEN=60 PROTO=TCP DPT=443")
    assert parse_log_line(good) == ("net-7.x", "93.184.216.34")
    assert parse_log_line("audit: unrelated kernel noise") is None
    assert parse_log_line("hearth-egress lost-id no dst here") is None
    assert parse_log_line("") is None

    # watch: temp-file source, burst dedup, rows land in egress_log
    d = tempfile.mkdtemp(prefix="hearth-egress-")
    logf = os.path.join(d, "kern.log")
    db = os.path.join(d, "audit.db")
    with open(logf, "w") as fh:
        fh.write(good + "\n")
        fh.write(good + "\n")  # burst duplicate inside the 10s window
        fh.write("audit: unrelated kernel noise\n")
        fh.write("hearth-egress net-9 IN= OUT=eth0 SRC=10.0.0.9 "
                 "DST=2606:2800:220:1::1 PROTO=TCP DPT=443\n")
    buf3 = io.StringIO()
    with contextlib.redirect_stdout(buf3):
        rc3 = main(["watch", "--from-file", logf, "--db", db])
    assert rc3 == 0 and "recorded 2 blocked" in buf3.getvalue()
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT agent_id, tool, host, url, allowed FROM egress_log ORDER BY id").fetchall()
    con.close()
    assert len(rows) == 2, ("burst duplicate must be deduped", rows)
    assert rows[0] == ("net-7.x", "os", "93.184.216.34", "", 0), rows[0]
    assert rows[1] == ("net-9", "os", "2606:2800:220:1::1", "", 0), rows[1]
    # the same (id, dst) after the window has passed writes again
    fake_now = [0.0]
    n = watch_lines([good, good], db, window=10.0,
                    clock=lambda: fake_now.__setitem__(0, fake_now[0] + 11) or fake_now[0])
    assert n == 2, "outside the window the pair must be written again"

    print("hearth-egress self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-egress")
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")
    ap = sub.add_parser("apply", help="load per-run nftables egress rules")
    ap.add_argument("--id", required=True, help="run id (hearth-agent@<id>)")
    ap.add_argument("--hosts", default="", help="comma-separated allowed hosts")
    ap.add_argument("--dry-run", action="store_true",
                    help="print the generated ruleset instead of loading it")
    rp = sub.add_parser("remove", help="tear down a run's egress rules")
    rp.add_argument("--id", required=True)
    wp = sub.add_parser("watch", help="record kernel-log drops to egress_log")
    wp.add_argument("--from-file", default="",
                    help="read log lines from a file instead of journalctl")
    wp.add_argument("--db", default=DEFAULT_DB)
    wp.add_argument("--window", type=float, default=DEDUP_WINDOW_S,
                    help="dedup window in seconds for repeated (id, dst) drops")
    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()
    if a.cmd == "apply":
        return apply_rules(a.id, a.hosts, dry_run=a.dry_run)
    if a.cmd == "remove":
        return remove_rules(a.id)
    if a.cmd == "watch":
        return cmd_watch(a.from_file, a.db, a.window)
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
