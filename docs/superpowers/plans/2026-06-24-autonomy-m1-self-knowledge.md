# Autonomy Milestone 1, Plan 1: Self-Knowledge Toolset Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Give agents read-only tools to understand the box before they ever change it: `list_generations`, `system_health`, `read_self_config`, `git_status`, `git_diff`. All classed `safe` (run in every mode, no approval).

**Architecture:** Five new entries in the existing tool registry (`agent/hearth_tools.py`), each split into a network/subprocess call plus pure parsing where possible. System binaries (`systemctl`, `git`, `nvidia-smi`) are resolved by PATH-then-absolute-NixOS-path because agents run as systemd units with a minimal PATH (same lesson as hearth-mapd's kill switch). Config/repo reads are rooted at `HEARTH_REPO` (default `/home/operator/hearth-desktop`, the deploy location) with a path-escape guard. Risk-classed `safe` in `agent/permissions.py`.

**Tech Stack:** Python 3 stdlib only. Tests via the in-module `_self_test()` convention (`python agent/hearth_tools.py`). Dev machine Windows (`python`). Blade deploy/verify at the end. This is Plan 1 of 4 in Autonomy Milestone 1 (vision: `docs/superpowers/specs/2026-06-24-hearth-autonomy-vision.md`); the swarm engine, mission-control map, and CI self-evolution follow.

**Commit identity:** `git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "<msg>"`. No AI attribution. No em-dashes.

**Working dir:** `C:/Users/ericc/hearth-wt` (branch worktree-desktop). Blade: `ssh operator@192.168.1.64`.

---

### Task 1: The five self-knowledge tools (`agent/hearth_tools.py`)

The complete code is below; implement via TDD (failing self-test first, then the code).

- [ ] **Step 1: Add the failing self-test** (append to `_self_test()` before its final `print`):
```python
    # self-knowledge tools: parsers are exact; tools never crash off-NixOS.
    assert _meminfo_summary("MemTotal: 16384000 kB\nMemAvailable: 8192000 kB\n") == (8000, 16000)
    try:
        _repo_join("../../etc/passwd")
        assert False, "expected escape guard to raise"
    except ValueError:
        pass
    for name in ("list_generations", "system_health", "git_status", "git_diff"):
        out = execute_tool(name, {}, ws)
        assert isinstance(out, str) and out, (name, out)
    assert isinstance(execute_tool("read_self_config", {"path": "flake.nix"}, ws), str)
```

- [ ] **Step 2: Run `python agent/hearth_tools.py`** -> expect NameError on `_meminfo_summary`.

- [ ] **Step 3: Add imports + helpers** near the top (after the existing imports):
```python
import glob
import shutil


def _bin(name, fallback):
    """Resolve a system binary by PATH, falling back to its NixOS stable path
    (agents may run as a systemd unit with a minimal PATH)."""
    return shutil.which(name) or fallback


HEARTH_REPO = os.environ.get("HEARTH_REPO", "/home/operator/hearth-desktop")
_SYSTEM_PROFILES = "/nix/var/nix/profiles"


def _meminfo_summary(text):
    """Parse /proc/meminfo into (used_mb, total_mb)."""
    vals = {}
    for ln in (text or "").splitlines():
        if ":" in ln:
            k, v = ln.split(":", 1)
            vals[k.strip()] = v.strip()

    def kb(key):
        try:
            return int(vals.get(key, "0").split()[0])
        except (ValueError, IndexError):
            return 0

    total = kb("MemTotal")
    avail = kb("MemAvailable")
    return (max(total - avail, 0)) // 1024, total // 1024


def _repo_join(path):
    """Resolve a path inside HEARTH_REPO, refusing escapes."""
    root = os.path.realpath(HEARTH_REPO)
    full = os.path.realpath(os.path.join(root, (path or "").lstrip("/")))
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path escapes the hearth repo: {}".format(path))
    return full
```
(`_parse_generation` from the current_generation tool is reused below; it already exists.)

- [ ] **Step 4: Add the five tool functions** (place among the other `tool_*` functions):
```python
def tool_list_generations(args, workspace):
    """List NixOS system generations (number + build date), newest first,
    marking the active one with a star."""
    from datetime import datetime, timezone
    try:
        current = _parse_generation(os.readlink(os.path.join(_SYSTEM_PROFILES, "system")))
    except OSError:
        current = "unknown"
    rows = []
    for link in glob.glob(os.path.join(_SYSTEM_PROFILES, "system-*-link")):
        num = _parse_generation(os.path.basename(link))
        if num == "unknown":
            continue
        try:
            ts = datetime.fromtimestamp(os.lstat(link).st_mtime, timezone.utc).isoformat()
        except OSError:
            ts = "unknown"
        rows.append((int(num), ts))
    if not rows:
        return "error: no system generations found (not a NixOS host?)"
    rows.sort(reverse=True)
    return "\n".join("{}{}  {}".format("* " if str(n) == current else "  ", n, ts)
                     for n, ts in rows)


def tool_system_health(args, workspace):
    """Report system health: systemd status, failed units, disk, memory, GPU."""
    parts = []
    systemctl = _bin("systemctl", "/run/current-system/sw/bin/systemctl")
    try:
        st = subprocess.run([systemctl, "is-system-running"],
                            capture_output=True, text=True, timeout=5)
        parts.append("system: {}".format((st.stdout or st.stderr or "?").strip()))
    except (OSError, subprocess.SubprocessError):
        parts.append("system: unknown")
    try:
        failed = subprocess.run([systemctl, "--failed", "--no-legend", "--plain"],
                                capture_output=True, text=True, timeout=5).stdout
        names = [ln.split()[0] for ln in failed.splitlines() if ln.strip()]
        parts.append("failed units: {}{}".format(
            len(names), (" (" + ", ".join(names[:5]) + ")") if names else ""))
    except (OSError, subprocess.SubprocessError):
        parts.append("failed units: unknown")
    try:
        du = shutil.disk_usage("/")
        parts.append("disk /: {}/{} GB used".format(du.used // (1024**3), du.total // (1024**3)))
    except OSError:
        pass
    try:
        with open("/proc/meminfo") as fh:
            used, total = _meminfo_summary(fh.read())
        parts.append("mem: {}/{} MB used".format(used, total))
    except OSError:
        pass
    nvidia = shutil.which("nvidia-smi")
    if nvidia:
        try:
            g = subprocess.run(
                [nvidia, "--query-gpu=utilization.gpu,memory.used,memory.total",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5).stdout.strip()
            if g:
                parts.append("gpu: " + g.splitlines()[0])
        except (OSError, subprocess.SubprocessError):
            pass
    return "\n".join(parts)


def tool_read_self_config(args, workspace):
    """Read a file from hearth's own configuration repo (the flake source)."""
    try:
        full = _repo_join(args.get("path", ""))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        with open(full) as fh:
            return fh.read()[:MAX_OUT]
    except OSError as exc:
        return "error: {}".format(exc)


def tool_git_status(args, workspace):
    """Show git status of hearth's configuration repo."""
    git = _bin("git", "/run/current-system/sw/bin/git")
    try:
        r = subprocess.run([git, "-C", HEARTH_REPO, "status", "--short", "--branch"],
                           capture_output=True, text=True, timeout=15)
        return (r.stdout or r.stderr or "(clean)")[:MAX_OUT]
    except (OSError, subprocess.SubprocessError) as exc:
        return "error: {}".format(exc)


def tool_git_diff(args, workspace):
    """Show git diff of hearth's configuration repo (optionally for one path)."""
    git = _bin("git", "/run/current-system/sw/bin/git")
    cmd = [git, "-C", HEARTH_REPO, "diff"]
    if args.get("path"):
        cmd += ["--", args["path"]]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        return (r.stdout or "(no changes)")[:MAX_OUT]
    except (OSError, subprocess.SubprocessError) as exc:
        return "error: {}".format(exc)
```

- [ ] **Step 5: Register all five in `TOOLS`** (add these dicts to the list):
```python
    {
        "name": "list_generations",
        "description": "List NixOS system generations (number and build date), marking the active one. Read-only.",
        "parameters": {"type": "object", "properties": {}},
        "fn": tool_list_generations,
    },
    {
        "name": "system_health",
        "description": "Report system health: systemd status, failed units, disk, memory, GPU. Read-only.",
        "parameters": {"type": "object", "properties": {}},
        "fn": tool_system_health,
    },
    {
        "name": "read_self_config",
        "description": "Read a file from hearth's own NixOS configuration repo (the flake source). Provide path relative to the repo root.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
        "fn": tool_read_self_config,
    },
    {
        "name": "git_status",
        "description": "Show git status of hearth's configuration repo. Read-only.",
        "parameters": {"type": "object", "properties": {}},
        "fn": tool_git_status,
    },
    {
        "name": "git_diff",
        "description": "Show git diff of hearth's configuration repo, optionally for one path. Read-only.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "fn": tool_git_diff,
    },
```

- [ ] **Step 6: Run `python agent/hearth_tools.py`** -> expect `hearth-tools self-test OK`.

- [ ] **Step 7: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/hearth_tools.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: self-knowledge agent tools (generations, health, self-config, git status/diff)"
```

---

### Task 2: Risk-class the new tools `safe` (`agent/permissions.py`)

- [ ] **Step 1: Add failing assertions** in `_self_test()`:
```python
    for t in ("list_generations", "system_health", "read_self_config", "git_status", "git_diff"):
        assert risk_of(t) == "safe", t
        assert decide("plan", t) == "allow", t
```
- [ ] **Step 2: Run `python agent/permissions.py`** -> these may already pass? No: unknown tools default to `dangerous`, so `risk_of("list_generations")` is `dangerous` until added -> assertion fails. Confirm the failure.
- [ ] **Step 3: Add to the `RISK` dict** (next to the other `safe` entries):
```python
    "list_generations": "safe",
    "system_health": "safe",
    "read_self_config": "safe",
    "git_status": "safe",
    "git_diff": "safe",
```
- [ ] **Step 4: Run `python agent/permissions.py`** -> `hearth-permissions self-test OK`.
- [ ] **Step 5: Commit**
```bash
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" add agent/permissions.py
git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" commit -m "feat: classify self-knowledge tools as safe (read-only, every mode)"
```

---

### Task 3: Deploy to the blade, verify, push

- [ ] **Step 1: Local gate**
```bash
python agent/permissions.py && python agent/hearth_tools.py && python agent/hearth_loop.py --self-test >/dev/null 2>&1 && echo loop-ok && python webui/hearth_mapd.py --self-test
```
All print OK.

- [ ] **Step 2: Deploy**
```bash
cd C:/Users/ericc/hearth-wt
git archive -o C:/Users/ericc/AppData/Local/Temp/wt.tar HEAD
for i in 1 2 3 4; do scp -o ConnectTimeout=25 C:/Users/ericc/AppData/Local/Temp/wt.tar operator@192.168.1.64:~/wt.tar && break || sleep 10; done
ssh -o ConnectTimeout=30 operator@192.168.1.64 'rm -rf ~/hearth-desktop && mkdir -p ~/hearth-desktop && tar -xf ~/wt.tar -C ~/hearth-desktop && cd ~/hearth-desktop && sudo nixos-rebuild switch --flake ~/hearth-desktop#blade 2>&1 | tail -1'
```

- [ ] **Step 3: Verify each tool against real data** (direct, via the deployed source)
```bash
ssh operator@192.168.1.64 'cd ~/hearth-desktop && for t in list_generations system_health git_status; do echo "=== $t ==="; python3 -c "import sys;sys.path.insert(0,\"agent\");import hearth_tools as m;print(m.execute_tool(\"'"$t"'\",{},\"/tmp\"))"; done; echo "=== read_self_config flake.nix (first 3 lines) ==="; python3 -c "import sys;sys.path.insert(0,\"agent\");import hearth_tools as m;print(m.execute_tool(\"read_self_config\",{\"path\":\"flake.nix\"},\"/tmp\")[:200])"'
```
Expected: `list_generations` lists generations with one starred; `system_health` shows `system: running` (or degraded), failed-unit count, disk, mem, gpu; `git_status` shows the repo branch; `read_self_config` returns the top of flake.nix. Note HEARTH_REPO defaults to `/home/operator/hearth-desktop` which is the deploy dir, so git/config tools target the deployed tree.

- [ ] **Step 4: One agent run** (proves the tools through the loop)
```bash
ssh operator@192.168.1.64 'curl -s -X POST localhost:8770/run -H "Content-Type: application/json" -d "{\"name\":\"selfknow\",\"model\":\"qwen2.5-coder:latest\",\"mode\":\"bypass\",\"prompt\":\"Use system_health and list_generations, then give me a one-paragraph status of this machine.\"}"; sleep 50; python3 -c "import sqlite3,json;c=sqlite3.connect(\"/var/lib/hearth/runs/audit.db\");[print((json.loads(r[0]).get(\"content\") or \"\")[:240]) for r in c.execute(\"select event from agent_transcript where agent_id like \"+chr(39)+\"selfknow-%\"+chr(39)+\" and event like \"+chr(39)+\"%message%\"+chr(39)+\" order by id desc limit 1\")]"'
```
Expected: the agent reports a real machine status synthesized from the tools.

- [ ] **Step 5: Push**
```bash
cd C:/Users/ericc/hearth-wt && git fetch origin && git -c user.name="eric catalano" -c user.email="eric.catalano925@gmail.com" merge origin/main -m "merge: main before self-knowledge tools" && git push origin worktree-desktop:main
```

---

## Self-Review
- Coverage: 5 tools (Task 1) + risk classing (Task 2) + blade verification (Task 3). All read-only/`safe`.
- Placeholders: none; complete code; exact verify commands.
- Consistency: `_bin`, `HEARTH_REPO`, `_repo_join`, `_meminfo_summary`, `_parse_generation` (reused) referenced consistently; all 5 names appear in both `TOOLS` and `permissions.RISK`. System binaries resolved by `_bin` (PATH then NixOS absolute) to survive the minimal-PATH systemd environment.
- Note: `read_self_config`/`git_*` target `HEARTH_REPO` (deploy dir), giving agents read access to hearth's own source. This is the foundation the CI self-evolution plan builds on (an agent reads the flake before proposing a change).
