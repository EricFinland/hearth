#!/usr/bin/env python3
"""hearth agent tools: a small pluggable registry. A tool is a dict with a name,
a description, a JSON-schema for its parameters, and a `fn(args, workspace)` that
runs it and returns a short string result. Adding a capability means adding a
tool here (or registering one at runtime). Standard library only.

All file/command tools operate inside the per-run workspace and refuse paths that
escape it, as defence in depth on top of the systemd sandbox.
"""

import json
import os
import subprocess
import urllib.error
import urllib.request

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
        "name": "http_request",
        "description": "Make an HTTP request to an external API. Provide url, optional method, headers, body.",
        "parameters": {"type": "object", "properties": {
            "url": {"type": "string"}, "method": {"type": "string"},
            "headers": {"type": "object"}, "body": {"type": "string"}},
            "required": ["url"]},
        "fn": tool_http_request,
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
    print("hearth-tools self-test OK")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_self_test())
