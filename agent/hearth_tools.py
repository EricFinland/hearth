#!/usr/bin/env python3
"""hearth agent tools: a small pluggable registry. A tool is a dict with a name,
a description, a JSON-schema for its parameters, and a `fn(args, workspace)` that
runs it and returns a short string result. Adding a capability means adding a
tool here (or registering one at runtime). Standard library only.

All file/command tools operate inside the per-run workspace and refuse paths that
escape it, as defence in depth on top of the systemd sandbox.
"""

import html as _htmlmod
import json
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

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

    print("hearth-tools self-test OK")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
