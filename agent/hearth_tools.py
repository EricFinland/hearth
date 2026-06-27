#!/usr/bin/env python3
"""hearth agent tools: a small pluggable registry. A tool is a dict with a name,
a description, a JSON-schema for its parameters, and a `fn(args, workspace)` that
runs it and returns a short string result. Adding a capability means adding a
tool here (or registering one at runtime). Standard library only.

All file/command tools operate inside the per-run workspace and refuse paths that
escape it, as defence in depth on top of the systemd sandbox.
"""

import fnmatch
import glob
import html as _htmlmod
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


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

class _TextExtractor(HTMLParser):
    _SKIP = {"script", "style", "head", "noscript"}

    def __init__(self):
        super().__init__()
        self.parts = []
        self._skip_depth = 0

    def handle_starttag(self, tag, attrs):
        if tag in self._SKIP:
            self._skip_depth += 1

    def handle_endtag(self, tag):
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data):
        if self._skip_depth == 0 and data.strip():
            self.parts.append(data)


def _html_to_text(html_text):
    p = _TextExtractor()
    try:
        p.feed(html_text or "")
    except Exception:  # noqa: BLE001 - malformed HTML must not raise
        pass
    return " ".join(" ".join(part.split()) for part in p.parts if part.strip())


COMMAND_TIMEOUT = 120
HTTP_TIMEOUT = 30
MAX_OUT = 4000


def _safe_join(workspace, path):
    path = (path or "").lstrip("/")
    full = os.path.realpath(os.path.join(workspace, path))
    root = os.path.realpath(workspace)
    if full != root and not full.startswith(root + os.sep):
        raise ValueError("path escapes workspace: {}".format(path))
    return full


def tool_run_command(args, workspace):
    cmd = args.get("command", "")
    if not cmd:
        return "error: no command"
    try:
        r = subprocess.run(cmd, shell=True, cwd=workspace, capture_output=True,
                           text=True, timeout=COMMAND_TIMEOUT)
        out = (r.stdout or "")[-MAX_OUT:]
        err = (r.stderr or "")[-2000:]
        return "exit={}\nstdout:\n{}\nstderr:\n{}".format(r.returncode, out, err)
    except subprocess.TimeoutExpired:
        return "error: command timed out after {}s".format(COMMAND_TIMEOUT)
    except OSError as exc:
        return "error: {}".format(exc)


def tool_write_file(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path"))
    except ValueError as exc:
        return "error: {}".format(exc)
    content = args.get("content", "")
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(full, "w") as fh:
        fh.write(content)
    return "wrote {} ({} bytes)".format(args.get("path"), len(content))


def tool_read_file(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path"))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        with open(full) as fh:
            return fh.read()[:MAX_OUT]
    except OSError as exc:
        return "error: {}".format(exc)


def tool_list_files(args, workspace):
    try:
        full = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        return "\n".join(sorted(os.listdir(full))) or "(empty)"
    except OSError as exc:
        return "error: {}".format(exc)


_TREE_SKIP = {".git", "__pycache__", "node_modules", "result", ".direnv", ".mypy_cache"}


def tool_search_files(args, workspace):
    """Search files under a path for a regex (or literal) query. Returns
    relpath:lineno: line for each match, capped."""
    query = args.get("query", "")
    if not query:
        return "error: no query"
    try:
        base = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        rx = re.compile(query)
    except re.error:
        rx = re.compile(re.escape(query))
    pattern = args.get("glob")
    root = os.path.realpath(workspace)
    hits = []
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _TREE_SKIP]
        for fn in sorted(files):
            if pattern and not fnmatch.fnmatch(fn, pattern):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue
                with open(fp, "r", errors="replace") as fh:
                    for i, line in enumerate(fh, 1):
                        if rx.search(line):
                            hits.append("{}:{}: {}".format(os.path.relpath(fp, root), i, line.rstrip()[:200]))
                            if len(hits) >= 200:
                                return "\n".join(hits) + "\n... (truncated at 200 matches)"
            except OSError:
                continue
    return "\n".join(hits) if hits else "no matches"


def tool_list_tree(args, workspace):
    """Render an indented directory tree under a path (common build/VCS dirs skipped)."""
    try:
        base = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    try:
        max_depth = max(1, int(args.get("max_depth", 4) or 4))
    except (TypeError, ValueError):
        max_depth = 4
    root = os.path.realpath(workspace)
    base_depth = base.rstrip(os.sep).count(os.sep)
    lines = []
    for dirpath, dirs, files in os.walk(base):
        depth = dirpath.rstrip(os.sep).count(os.sep) - base_depth
        if depth >= max_depth:
            dirs[:] = []
            continue
        dirs[:] = sorted(d for d in dirs if d not in _TREE_SKIP)
        rel = os.path.relpath(dirpath, root)
        indent = "  " * depth
        lines.append("{}{}/".format(indent, "." if rel == "." else os.path.basename(dirpath)))
        for fn in sorted(files):
            lines.append("{}  {}".format(indent, fn))
        if len(lines) >= 400:
            lines.append("... (truncated)")
            break
    return "\n".join(lines) or "(empty)"


def tool_edit_file(args, workspace):
    """Edit a file by replacing exact text: find -> replace. Replaces the first
    match, or every match when all=true. Errors (no change) if find is absent, so
    the model knows to re-read the file. More reliable for local models than diffs."""
    try:
        full = _safe_join(workspace, args.get("path"))
    except ValueError as exc:
        return "error: {}".format(exc)
    find = args.get("find")
    if not find:
        return "error: no 'find' text given"
    replace = args.get("replace", "")
    try:
        with open(full) as fh:
            content = fh.read()
    except OSError as exc:
        return "error: {}".format(exc)
    count = content.count(find)
    if count == 0:
        return "error: 'find' text not found in {}; re-read the file and try again (no changes made)".format(args.get("path"))
    if args.get("all"):
        content = content.replace(find, replace)
        n = count
    else:
        content = content.replace(find, replace, 1)
        n = 1
    with open(full, "w") as fh:
        fh.write(content)
    return "edited {}: {} replacement{}".format(args.get("path"), n, "s" if n != 1 else "")


def _resolve_cred(name):
    """Read a stored credential by name from the systemd credentials directory.
    The credentials file is a simple `NAME=VALUE` per line. Returns "" if not
    available, so an agent can never read the raw store directly."""
    allowed = os.environ.get("HEARTH_ALLOWED_CREDS")
    if allowed is not None and name not in [a.strip() for a in allowed.split(",") if a.strip()]:
        return ""
    creds_dir = os.environ.get("CREDENTIALS_DIRECTORY")
    if not creds_dir:
        return ""
    path = os.path.join(creds_dir, "creds")
    try:
        with open(path) as fh:
            for line in fh:
                if line.startswith(name + "="):
                    return line.split("=", 1)[1].strip()
    except OSError:
        return ""
    return ""


def tool_web_fetch(args, workspace):
    url = args.get("url")
    if not url:
        return "error: no url"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (hearth-agent)"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            raw = resp.read(2000000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)
    text = _html_to_text(raw)
    return text[:MAX_OUT] if text else "(no readable text)"


def _ddg_real_url(href):
    """DDG result links are redirects like //duckduckgo.com/l/?uddg=<encoded>.
    Return the decoded target, or the href unchanged if already direct."""
    if "uddg=" in href:
        try:
            q = urllib.parse.urlparse("https:" + href if href.startswith("//") else href).query
            uddg = urllib.parse.parse_qs(q).get("uddg")
            if uddg:
                return urllib.parse.unquote(uddg[0])
        except (ValueError, KeyError):
            pass
    if href.startswith("//"):
        return "https:" + href
    return href


def _parse_ddg_results(html_text, max_results):
    """Extract [{title, url, snippet}] from DuckDuckGo HTML results."""
    results = []
    link_re = re.compile(r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.S)
    snip_re = re.compile(r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>', re.S)
    links = link_re.findall(html_text or "")
    snippets = snip_re.findall(html_text or "")

    def clean(s):
        return " ".join(_htmlmod.unescape(re.sub(r"<[^>]+>", "", s)).split())

    for i, (href, title) in enumerate(links[:max_results]):
        results.append({
            "title": clean(title),
            "url": _ddg_real_url(href),
            "snippet": clean(snippets[i]) if i < len(snippets) else "",
        })
    return results


def tool_web_search(args, workspace):
    query = args.get("query") or args.get("q")
    if not query:
        return "error: no query"
    max_results = int(args.get("max_results") or 5)
    # The DDG HTML endpoint serves results only for a POST with the query as form
    # data; a GET query string just returns the DDG home page.
    data = urllib.parse.urlencode({"q": query}).encode()
    req = urllib.request.Request(
        "https://html.duckduckgo.com/html/", data=data,
        headers={"User-Agent": "Mozilla/5.0 (hearth-agent)",
                 "Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            body = resp.read(2000000).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)
    results = _parse_ddg_results(body, max_results)
    if not results:
        return "(no results)"
    return "\n".join("{}. {}\n   {}\n   {}".format(i + 1, r["title"], r["url"], r["snippet"])
                     for i, r in enumerate(results))[:MAX_OUT]


def tool_http_request(args, workspace):
    url = args.get("url")
    if not url:
        return "error: no url"
    method = (args.get("method") or "GET").upper()
    headers = {k: (_resolve_cred(v[5:]) if isinstance(v, str) and v.startswith("cred:") else v)
               for k, v in (args.get("headers") or {}).items()}
    body = args.get("body")
    data = body.encode() if isinstance(body, str) else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            text = resp.read().decode("utf-8", "replace")[:MAX_OUT]
            return "status={}\n{}".format(resp.status, text)
    except urllib.error.HTTPError as exc:
        return "status={}\n{}".format(exc.code, exc.read().decode("utf-8", "replace")[:2000])
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return "error: {}".format(exc)


def _parse_generation(target):
    """Extract the generation number from a system profile link target like
    'system-123-link'. Returns 'unknown' if it does not match."""
    m = re.search(r"system-(\d+)-link", target or "")
    return m.group(1) if m else "unknown"


def tool_current_generation(args, workspace):
    """Report hearth's active NixOS generation number and its build date.
    Read-only introspection: follows /nix/var/nix/profiles/system."""
    profile = "/nix/var/nix/profiles/system"
    try:
        target = os.readlink(profile)
    except OSError as exc:
        return "error: cannot read the system profile ({})".format(exc)
    gen = _parse_generation(target)
    link = target if os.path.isabs(target) else os.path.join(os.path.dirname(profile), target)
    try:
        from datetime import datetime, timezone
        built = datetime.fromtimestamp(os.lstat(link).st_mtime, timezone.utc).isoformat()
    except OSError:
        built = "unknown"
    return "generation={}\nbuilt={}".format(gen, built)


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


def tool_write_self_config(args, workspace):
    """Write (create or overwrite) a file inside hearth's own configuration repo
    (HEARTH_REPO). Used by self-evolution to edit the flake. Refuses paths that
    escape the repo."""
    try:
        full = _repo_join(args.get("path", ""))
    except ValueError as exc:
        return "error: {}".format(exc)
    content = args.get("content", "")
    parent = os.path.dirname(full)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        with open(full, "w") as fh:
            fh.write(content)
    except OSError as exc:
        return "error: {}".format(exc)
    return "wrote {} ({} bytes) into the hearth repo".format(args.get("path"), len(content))


def tool_nix_check(args, workspace):
    """Validate hearth's flake by running `nix flake check --no-build` locally on
    HEARTH_REPO. Read-only (evaluates, does not change the system). Returns a line
    'nix_check PASS' or 'nix_check FAIL' plus the tail of the output, so an agent
    can see eval errors and fix them. The repo must be a flake (a flake.nix at its
    root)."""
    nix = _bin("nix", "/run/current-system/sw/bin/nix")
    try:
        r = subprocess.run(
            [nix, "--extra-experimental-features", "nix-command flakes",
             "flake", "check", "--no-build", HEARTH_REPO],
            capture_output=True, text=True, timeout=1200)
    except (OSError, subprocess.SubprocessError) as exc:
        return "error: {}".format(exc)
    tail = ((r.stdout or "") + "\n" + (r.stderr or "")).strip()[-MAX_OUT:]
    return "nix_check {}\n{}".format("PASS" if r.returncode == 0 else "FAIL", tail)


def tool_remember(args, workspace):
    """Record a lesson into hearth's self-learning memory."""
    import hearth_memory
    db = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
    rid = hearth_memory.remember(db, args.get("insight", ""), kind=args.get("kind", "lesson"),
                                 tags=args.get("tags", ""), source="tool")
    return "remembered (id {})".format(rid) if rid else "error: nothing to remember"


def tool_recall(args, workspace):
    """Recall lessons from hearth's memory relevant to a query."""
    import hearth_memory
    db = os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")
    hits = hearth_memory.recall(db, args.get("query", ""), limit=int(args.get("limit") or 8))
    if not hits:
        return "(no relevant lessons yet)"
    return "\n".join("[{}] {}".format(h["kind"], h["insight"]) for h in hits)


def _kb_db():
    return os.environ.get("HEARTH_DB", "/var/lib/hearth/runs/audit.db")


def tool_kb_add(args, workspace):
    """Add a document to the local knowledge base. Provide source plus either text
    or a workspace path to read from."""
    import hearth_knowledge
    source = (args.get("source") or "").strip()
    if not source:
        return "error: a 'source' name is required"
    text = args.get("text")
    if not text and args.get("path"):
        try:
            with open(_safe_join(workspace, args.get("path")), errors="replace") as fh:
                text = fh.read()
        except (OSError, ValueError) as exc:
            return "error: {}".format(exc)
    if not (text or "").strip():
        return "error: nothing to ingest (give 'text' or a readable 'path')"
    n = hearth_knowledge.ingest(_kb_db(), source, text, embed_fn=hearth_knowledge.make_embedder())
    return "added '{}' to the knowledge base ({} chunk{})".format(source, n, "s" if n != 1 else "")


def tool_replace_in_files(args, workspace):
    """Find/replace exact text across every matching file under a path (a
    multi-file refactor). Optional glob filters filenames. Returns a summary."""
    find = args.get("find")
    if not find:
        return "error: no 'find' text given"
    replace = args.get("replace", "")
    pattern = args.get("glob")
    try:
        base = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    files_changed = 0
    total = 0
    for dirpath, dirs, files in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _TREE_SKIP]
        for fn in sorted(files):
            if pattern and not fnmatch.fnmatch(fn, pattern):
                continue
            fp = os.path.join(dirpath, fn)
            try:
                if os.path.getsize(fp) > 2_000_000:
                    continue
                with open(fp, "r", errors="replace") as fh:
                    content = fh.read()
            except OSError:
                continue
            c = content.count(find)
            if c:
                with open(fp, "w") as fh:
                    fh.write(content.replace(find, replace))
                files_changed += 1
                total += c
    if not files_changed:
        return "no occurrences of that text found (no changes)"
    return "replaced {} occurrence(s) across {} file(s)".format(total, files_changed)


def tool_fetch_to_kb(args, workspace):
    """Fetch a web page's readable text and ingest it into the knowledge base in
    one step. Provide url (and optional source name)."""
    import hearth_knowledge
    url = args.get("url")
    if not url:
        return "error: no url"
    text = tool_web_fetch({"url": url}, workspace)
    if text.startswith("error:") or text == "(no readable text)":
        return "could not fetch: {}".format(text)
    source = args.get("source") or url
    n = hearth_knowledge.ingest(_kb_db(), source, text, embed_fn=hearth_knowledge.make_embedder())
    return "fetched and added '{}' to the knowledge base ({} chunk{})".format(source, n, "s" if n != 1 else "")


def tool_index_dir(args, workspace):
    """Index a directory (under the workspace) into the knowledge base so it can
    be searched. Provide name and path; optional comma-separated globs."""
    import hearth_project
    name = (args.get("name") or "").strip()
    if not name:
        return "error: a project 'name' is required"
    try:
        root = _safe_join(workspace, args.get("path", "."))
    except ValueError as exc:
        return "error: {}".format(exc)
    globs = None
    if args.get("globs"):
        globs = [g.strip() for g in str(args["globs"]).split(",") if g.strip()]
    import hearth_knowledge
    res = hearth_project.index_dir(_kb_db(), name, root, embed_fn=hearth_knowledge.make_embedder(), globs=globs)
    if res.get("error"):
        return "error: {}".format(res["error"])
    return ("indexed '{}': {} files, {} chunks ({} skipped){}").format(
        name, res["files"], res["chunks"], res["skipped"], ", truncated" if res["truncated"] else "")


def tool_kb_search(args, workspace):
    """Search the local knowledge base for chunks relevant to a query."""
    import hearth_knowledge
    hits = hearth_knowledge.search(_kb_db(), args.get("query", ""), limit=int(args.get("limit") or 5),
                                   embed_fn=hearth_knowledge.make_embedder())
    if not hits:
        return "(no relevant knowledge found)"
    return "\n\n".join("[{} #{}] (score {})\n{}".format(h["source"], h["chunk"], h["score"], h["text"])
                       for h in hits)


TOOLS = [
    {
        "name": "run_command",
        "description": "Run a shell command in the workspace. Use for building, testing, and inspecting code.",
        "parameters": {"type": "object", "properties": {
            "command": {"type": "string", "description": "the shell command"}},
            "required": ["command"]},
        "fn": tool_run_command,
    },
    {
        "name": "write_file",
        "description": "Write (create or overwrite) a file in the workspace.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
        "fn": tool_write_file,
    },
    {
        "name": "read_file",
        "description": "Read a file from the workspace.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                       "required": ["path"]},
        "fn": tool_read_file,
    },
    {
        "name": "list_files",
        "description": "List files in a workspace directory.",
        "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        "fn": tool_list_files,
    },
    {
        "name": "list_tree",
        "description": "Show an indented directory tree under a path (skips .git, __pycache__, node_modules). Use to understand a project's layout.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "max_depth": {"type": "integer"}}},
        "fn": tool_list_tree,
    },
    {
        "name": "search_files",
        "description": "Search files under a path for a regex or text query (like grep). Returns path:line: text. Optional glob filters filenames (e.g. *.py).",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "path": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["query"]},
        "fn": tool_search_files,
    },
    {
        "name": "edit_file",
        "description": "Edit a file by exact text replacement: replaces 'find' with 'replace' (first match, or all=true for every match). Errors without changing anything if 'find' is not present.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "find": {"type": "string"},
            "replace": {"type": "string"}, "all": {"type": "boolean"}},
            "required": ["path", "find"]},
        "fn": tool_edit_file,
    },
    {
        "name": "web_fetch",
        "description": "Fetch a web page and return its readable text (HTML tags stripped). Provide url.",
        "parameters": {"type": "object", "properties": {"url": {"type": "string"}},
                       "required": ["url"]},
        "fn": tool_web_fetch,
    },
    {
        "name": "web_search",
        "description": "Search the web and return top result titles, URLs, and snippets. Provide query.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "max_results": {"type": "integer"}},
            "required": ["query"]},
        "fn": tool_web_search,
    },
    {
        "name": "http_request",
        "description": "Make an HTTP request to an external API. Provide url, optional method, headers, body.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "method": {"type": "string"},
            "headers": {"type": "object"}, "body": {"type": "string"}},
            "required": ["url"]},
        "fn": tool_http_request,
    },
    {
        "name": "current_generation",
        "description": "Report hearth's active NixOS generation number and its build date. Read-only.",
        "parameters": {"type": "object", "properties": {}},
        "fn": tool_current_generation,
    },
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
    {
        "name": "write_self_config",
        "description": "Write (create or overwrite) a file inside hearth's own NixOS configuration repo, to propose a change to the system. Provide path (relative to the repo root) and content.",
        "parameters": {"type": "object", "properties": {
            "path": {"type": "string"}, "content": {"type": "string"}},
            "required": ["path", "content"]},
        "fn": tool_write_self_config,
    },
    {
        "name": "nix_check",
        "description": "Validate hearth's flake by running nix flake check locally (no build). Use after editing the config to confirm it still evaluates. Read-only.",
        "parameters": {"type": "object", "properties": {}},
        "fn": tool_nix_check,
    },
    {
        "name": "remember",
        "description": "Record a lesson into hearth's long-term memory so future runs can recall it. Provide insight (the lesson), optional kind and tags.",
        "parameters": {"type": "object", "properties": {
            "insight": {"type": "string"}, "kind": {"type": "string"}, "tags": {"type": "string"}},
            "required": ["insight"]},
        "fn": tool_remember,
    },
    {
        "name": "recall",
        "description": "Recall lessons from hearth's long-term memory relevant to a query.",
        "parameters": {"type": "object", "properties": {"query": {"type": "string"}}},
        "fn": tool_recall,
    },
    {
        "name": "kb_add",
        "description": "Add a document to the local knowledge base for later retrieval. Provide a source name and either text or a workspace path to read.",
        "parameters": {"type": "object", "properties": {
            "source": {"type": "string"}, "text": {"type": "string"}, "path": {"type": "string"}},
            "required": ["source"]},
        "fn": tool_kb_add,
    },
    {
        "name": "kb_search",
        "description": "Search the local knowledge base for the chunks most relevant to a query, to ground an answer in ingested documents.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string"}, "limit": {"type": "integer"}},
            "required": ["query"]},
        "fn": tool_kb_search,
    },
    {
        "name": "index_dir",
        "description": "Index a directory of code/text files (under the workspace) into the knowledge base so you can search it. Provide a project name and path. Use before kb_search to learn a codebase.",
        "parameters": {"type": "object", "properties": {
            "name": {"type": "string"}, "path": {"type": "string"}, "globs": {"type": "string"}},
            "required": ["name", "path"]},
        "fn": tool_index_dir,
    },
    {
        "name": "replace_in_files",
        "description": "Find/replace exact text across all matching files under a path (a multi-file refactor). Optional glob filters filenames (e.g. *.py).",
        "parameters": {"type": "object", "properties": {
            "find": {"type": "string"}, "replace": {"type": "string"},
            "path": {"type": "string"}, "glob": {"type": "string"}},
            "required": ["find"]},
        "fn": tool_replace_in_files,
    },
    {
        "name": "fetch_to_kb",
        "description": "Fetch a web page and add its readable text to the knowledge base in one step. Provide url (and optional source name).",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "source": {"type": "string"}},
            "required": ["url"]},
        "fn": tool_fetch_to_kb,
    },
]

_BY_NAME = {t["name"]: t for t in TOOLS}


def ollama_tool_specs():
    """The tools in Ollama's chat tool format."""
    return [{"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["parameters"]}}
        for t in TOOLS]


def execute_tool(name, args, workspace):
    tool = _BY_NAME.get(name)
    if tool is None:
        return "error: unknown tool {}".format(name)
    try:
        return tool["fn"](args or {}, workspace)
    except Exception as exc:  # noqa: BLE001 - a tool error must not crash the loop
        return "error: {}: {}".format(type(exc).__name__, exc)


def _self_test():
    import tempfile
    ws = tempfile.mkdtemp(prefix="hearth-tools-")
    assert "wrote" in execute_tool("write_file", {"path": "a/b.txt", "content": "hi"}, ws)
    assert execute_tool("read_file", {"path": "a/b.txt"}, ws) == "hi"
    assert "b.txt" in execute_tool("list_files", {"path": "a"}, ws)
    out = execute_tool("run_command", {"command": "echo hello"}, ws)
    assert "hello" in out and "exit=0" in out, out
    assert "escapes workspace" in execute_tool("write_file", {"path": "../evil", "content": "x"}, ws)
    assert len(ollama_tool_specs()) == len(TOOLS)
    # list_tree shows the layout; search_files greps; edit_file does find/replace.
    execute_tool("write_file", {"path": "src/app.py", "content": "x = 1\nprint(x)\n"}, ws)
    tree = execute_tool("list_tree", {}, ws)
    assert "src/" in tree and "app.py" in tree, tree
    hits = execute_tool("search_files", {"query": "print", "glob": "*.py"}, ws)
    assert "app.py" in hits and "print(x)" in hits, hits
    assert execute_tool("search_files", {"query": "nonexistent_zzz"}, ws) == "no matches"
    ed = execute_tool("edit_file", {"path": "src/app.py", "find": "x = 1", "replace": "x = 42"}, ws)
    assert "1 replacement" in ed, ed
    assert "x = 42" in execute_tool("read_file", {"path": "src/app.py"}, ws)
    miss = execute_tool("edit_file", {"path": "src/app.py", "find": "not there", "replace": "y"}, ws)
    assert "not found" in miss, ("edit_file errors cleanly when find absent", miss)
    # knowledge base: add docs (inline + from a workspace file), then search.
    import tempfile as _tf2
    kbdb = os.path.join(_tf2.mkdtemp(prefix="hearth-kbtool-"), "a.db")
    old_db = os.environ.get("HEARTH_DB")
    old_embed = os.environ.get("HEARTH_EMBED_MODEL")
    os.environ["HEARTH_DB"] = kbdb
    os.environ["HEARTH_EMBED_MODEL"] = ""  # force deterministic offline TF-IDF in the test
    try:
        assert "added" in execute_tool("kb_add", {"source": "nix", "text": "NixOS rolls back atomically from a flake."}, ws)
        execute_tool("write_file", {"path": "notes.txt", "content": "Ollama serves local models on the GPU."}, ws)
        assert "added" in execute_tool("kb_add", {"source": "notes", "path": "notes.txt"}, ws)
        found = execute_tool("kb_search", {"query": "rollback flake"}, ws)
        assert "nix" in found and "atomically" in found, found
        # index_dir: ingest a workspace subtree, then it is searchable
        execute_tool("write_file", {"path": "proj/readme.md", "content": "The widget service handles billing events."}, ws)
        idx = execute_tool("index_dir", {"name": "proj", "path": "proj"}, ws)
        assert "indexed 'proj'" in idx and "1 files" in idx, idx
        assert "billing" in execute_tool("kb_search", {"query": "widget billing events"}, ws)
        assert "no relevant" in execute_tool("kb_search", {"query": "zzz_nothing_qqq"}, ws)
        # replace_in_files: multi-file find/replace
        execute_tool("write_file", {"path": "r/a.py", "content": "VER = 1\n"}, ws)
        execute_tool("write_file", {"path": "r/b.py", "content": "x = VER\nVER = 2\n"}, ws)
        rep = execute_tool("replace_in_files", {"find": "VER", "replace": "VERSION", "path": "r", "glob": "*.py"}, ws)
        assert "3 occurrence" in rep and "2 file" in rep, rep
        assert "VERSION = 1" in execute_tool("read_file", {"path": "r/a.py"}, ws)
        assert execute_tool("replace_in_files", {"find": "nope_zzz"}, ws) == "no occurrences of that text found (no changes)"
    finally:
        if old_db is None:
            os.environ.pop("HEARTH_DB", None)
        else:
            os.environ["HEARTH_DB"] = old_db
        if old_embed is None:
            os.environ.pop("HEARTH_EMBED_MODEL", None)
        else:
            os.environ["HEARTH_EMBED_MODEL"] = old_embed
    # web_fetch: the HTML-to-text helper strips tags and collapses whitespace.
    sample = "<html><head><style>x{}</style><script>var a=1;</script></head>" \
             "<body><h1>Title</h1><p>Hello   world</p><p>Line two</p></body></html>"
    txt = _html_to_text(sample)
    assert "Title" in txt and "Hello world" in txt and "Line two" in txt, txt
    assert "var a=1" not in txt and "x{}" not in txt, ("script/style stripped", txt)
    # web_search: the DDG result parser extracts title/url/snippet from result HTML.
    ddg = ('<div class="result"><a class="result__a" '
           'href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fnixos.org%2F&rut=x">NixOS</a>'
           '<a class="result__snippet">Reproducible builds and deployments.</a></div>')
    res = _parse_ddg_results(ddg, 5)
    assert res and res[0]["title"] == "NixOS", res
    assert res[0]["url"] == "https://nixos.org/", res
    assert "Reproducible" in res[0]["snippet"], res
    # Credential scoping: HEARTH_ALLOWED_CREDS limits which creds resolve.
    import tempfile as _tf
    cdir = _tf.mkdtemp(prefix="hearth-creds-")
    with open(os.path.join(cdir, "creds"), "w") as fh:
        fh.write("alpha=secretA\nbravo=secretB\n")
    old_cd = os.environ.get("CREDENTIALS_DIRECTORY")
    old_allow = os.environ.get("HEARTH_ALLOWED_CREDS")
    os.environ["CREDENTIALS_DIRECTORY"] = cdir
    try:
        os.environ.pop("HEARTH_ALLOWED_CREDS", None)
        assert _resolve_cred("alpha") == "secretA", "no allow-list: all resolve"
        assert _resolve_cred("bravo") == "secretB", "no allow-list: all resolve"
        os.environ["HEARTH_ALLOWED_CREDS"] = "alpha"
        assert _resolve_cred("alpha") == "secretA", "allowed cred resolves"
        assert _resolve_cred("bravo") == "", "disallowed cred is withheld"
    finally:
        if old_cd is None:
            os.environ.pop("CREDENTIALS_DIRECTORY", None)
        else:
            os.environ["CREDENTIALS_DIRECTORY"] = old_cd
        if old_allow is None:
            os.environ.pop("HEARTH_ALLOWED_CREDS", None)
        else:
            os.environ["HEARTH_ALLOWED_CREDS"] = old_allow

    # current_generation: parse the generation number, and confirm the tool
    # never crashes even where the system profile is absent (dev/non-NixOS).
    assert _parse_generation("system-123-link") == "123"
    assert _parse_generation("garbage") == "unknown"
    cg = execute_tool("current_generation", {}, ws)
    assert isinstance(cg, str) and cg, cg

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

    # self-evolution tools: write into the repo (guarded), and nix_check never crashes.
    import tempfile as _tfe
    repo = _tfe.mkdtemp(prefix="hearth-selfrepo-")
    _old_repo = globals()["HEARTH_REPO"]
    globals()["HEARTH_REPO"] = repo
    try:
        out = execute_tool("write_self_config", {"path": "sub/x.nix", "content": "# hi\n"}, ws)
        assert "wrote" in out, out
        with open(os.path.join(repo, "sub", "x.nix")) as fh:
            assert fh.read() == "# hi\n"
        assert "escapes" in execute_tool("write_self_config", {"path": "../evil", "content": "x"}, ws)
        nc = execute_tool("nix_check", {}, ws)
        assert isinstance(nc, str) and nc, nc  # on dev (no nix) it returns an error string, never crashes
    finally:
        globals()["HEARTH_REPO"] = _old_repo

    # memory tools round-trip via the shared store (uses HEARTH_DB env).
    import tempfile as _tfm2
    _mdb = os.path.join(_tfm2.mkdtemp(prefix="hearth-memtool-"), "a.db")
    _oldhdb = os.environ.get("HEARTH_DB")
    os.environ["HEARTH_DB"] = _mdb
    try:
        assert "remembered" in execute_tool("remember", {"insight": "always run nix_check before merging"}, ws)
        assert "nix_check" in execute_tool("recall", {"query": "nix_check merging"}, ws)
    finally:
        if _oldhdb is None:
            os.environ.pop("HEARTH_DB", None)
        else:
            os.environ["HEARTH_DB"] = _oldhdb

    print("hearth-tools self-test OK")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
