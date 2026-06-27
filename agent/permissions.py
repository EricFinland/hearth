#!/usr/bin/env python3
"""hearth permission engine: decide whether an agent may run a tool given the
current permission mode. Pure and I/O-free so it is trivially testable and shared
by every drive path (interactive sessions and background workers).

Permission modes:
  plan   - read-only; the agent may look but change nothing, then must produce a plan.
  auto   - safe reads and file edits run automatically; dangerous actions are gated
           (the user must approve each one).
  bypass - everything runs, no prompts.

Decision values:
  "allow" - run the tool now
  "gate"  - pause and ask the user to approve or deny
  "deny"  - refuse outright (and tell the model why)
"""

import sys

MODES = ("plan", "auto", "bypass")

# Risk class per tool: "safe" (reads), "edit" (file writes), "dangerous"
# (shell, network, sudo). Unknown tools are treated as dangerous (fail closed).
RISK = {
    "read_file": "safe",
    "list_files": "safe",
    "list_tree": "safe",
    "search_files": "safe",
    "edit_file": "edit",
    "current_generation": "safe",
    "list_generations": "safe",
    "system_health": "safe",
    "read_self_config": "safe",
    "git_status": "safe",
    "git_diff": "safe",
    "write_file": "edit",
    "run_command": "dangerous",
    "http_request": "dangerous",
    "web_search": "dangerous",
    "web_fetch": "dangerous",
    "nix_check": "safe",
    "write_self_config": "edit",
    "remember": "safe",
    "recall": "safe",
    "kb_search": "safe",
    "kb_add": "edit",
    "replace_in_files": "edit",
    "fetch_to_kb": "dangerous",
    "index_dir": "edit",
}


def risk_of(tool):
    return RISK.get(tool, "dangerous")


def _command_head(args):
    cmd = ((args or {}).get("command") or "").strip()
    return cmd.split()[0] if cmd else ""


def decide(mode, tool, args=None, auto_allow=()):
    """Return 'allow' | 'gate' | 'deny' for (mode, tool, args).

    auto_allow is an optional collection of command heads (for example
    {'git', 'ls'}) that run automatically even in auto mode. Empty by default.
    auto_allow only applies to run_command command heads; it does not affect other dangerous tools such as http_request.
    """
    if mode not in MODES:
        return "gate"  # invalid modes fail safe by gating
    risk = risk_of(tool)
    if mode == "bypass":
        return "allow"
    if mode == "plan":
        return "allow" if risk == "safe" else "deny"
    # auto
    if risk in ("safe", "edit"):
        return "allow"
    if tool == "run_command" and _command_head(args) in set(auto_allow):
        return "allow"
    return "gate"


def _self_test():
    # bypass: everything allowed
    for t in ("read_file", "write_file", "run_command", "http_request", "mystery"):
        assert decide("bypass", t) == "allow", t
    # plan: only safe reads, everything else denied
    assert decide("plan", "read_file") == "allow"
    assert decide("plan", "list_files") == "allow"
    assert decide("plan", "write_file") == "deny"
    assert decide("plan", "run_command") == "deny"
    assert decide("plan", "http_request") == "deny"
    # auto: safe and edit allowed, dangerous gated
    assert decide("auto", "read_file") == "allow"
    assert decide("auto", "write_file") == "allow"
    assert decide("auto", "run_command") == "gate"
    assert decide("auto", "http_request") == "gate"
    # auto + allowlist: a whitelisted command head runs automatically
    assert decide("auto", "run_command", {"command": "git status"}, auto_allow={"git"}) == "allow"
    assert decide("auto", "run_command", {"command": "rm -rf /"}, auto_allow={"git"}) == "gate"
    # unknown tool fails closed (dangerous)
    assert risk_of("mystery") == "dangerous"
    assert decide("auto", "mystery") == "gate"
    # unknown mode -> gate (safest)
    assert decide("yolo", "read_file") == "gate"
    assert risk_of("web_search") == "dangerous", "web_search should be dangerous"
    assert risk_of("web_fetch") == "dangerous", "web_fetch should be dangerous"
    assert decide("auto", "web_search") == "gate"
    assert decide("bypass", "web_fetch") == "allow"
    assert decide("plan", "web_search") == "deny"
    assert risk_of("nix_check") == "safe"
    assert risk_of("write_self_config") == "edit"
    assert decide("plan", "write_self_config") == "deny"  # plan mode changes nothing
    assert decide("bypass", "write_self_config") == "allow"
    # current_generation is read-only introspection: safe in every mode.
    assert risk_of("current_generation") == "safe"
    assert decide("plan", "current_generation") == "allow"
    assert decide("auto", "current_generation") == "allow"
    for t in ("list_generations", "system_health", "read_self_config", "git_status", "git_diff"):
        assert risk_of(t) == "safe", t
        assert decide("plan", t) == "allow", t
    assert risk_of("remember") == "safe" and risk_of("recall") == "safe"
    assert risk_of("list_tree") == "safe" and risk_of("search_files") == "safe"
    assert risk_of("edit_file") == "edit"
    assert decide("plan", "search_files") == "allow" and decide("plan", "edit_file") == "deny"
    assert decide("auto", "edit_file") == "allow"
    assert risk_of("kb_search") == "safe" and risk_of("kb_add") == "edit"
    assert decide("plan", "kb_search") == "allow" and decide("plan", "kb_add") == "deny"
    assert risk_of("replace_in_files") == "edit" and risk_of("fetch_to_kb") == "dangerous"
    assert decide("auto", "replace_in_files") == "allow" and decide("auto", "fetch_to_kb") == "gate"
    assert decide("plan", "recall") == "allow"
    print("hearth-permissions self-test OK")
    return 0


if __name__ == "__main__":
    sys.exit(_self_test())
