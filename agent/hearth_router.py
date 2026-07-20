#!/usr/bin/env python3
"""hearth router: declarative model selection for "auto" launches.

A rule-based map from a goal (and the tools a run was granted) to which local
model should run it, so cheap models handle easy tasks and a coder or larger
model handles the harder ones. The rules are declared once, rendered by the
system configuration to a read-only JSON file, and consulted by every launch
that asks for model "auto".

Rules come from /etc/hearth/router.json (env HEARTH_ROUTER overrides the path).
A missing or invalid file yields an empty ruleset, which makes the router a
no-op: it just returns the caller's default. The file shape (the contract with
the system configuration agent) is:

  {
    "default": "llama3.2:3b",
    "rules": [
      {"name": "code",
       "any_keywords": ["code", "refactor", "python", "bug"],
       "tools_any": ["edit_file", "replace_in_files"],
       "model": "qwen2.5-coder:latest"},
      {"name": "research",
       "any_keywords": ["research", "summarize", "analyze"],
       "model": "qwen2.5:7b-instruct"}
    ]
  }

The selection is pure and deterministic (you pass the goal, the tools, and the
rules), so it is fully testable with no config file on disk. Standard library
only.

Config:
  HEARTH_ROUTER   path to the rules JSON; default /etc/hearth/router.json
"""

import argparse
import json
import os
import sys

DEFAULT_RULES = os.environ.get("HEARTH_ROUTER", "/etc/hearth/router.json")


def _tools_list(tools):
    """Normalize a tools value (list, comma string, or None) to a list of
    stripped non-empty strings."""
    if tools is None:
        return []
    if isinstance(tools, str):
        parts = tools.split(",")
    else:
        parts = list(tools)
    out = []
    for p in parts:
        s = str(p).strip()
        if s:
            out.append(s)
    return out


def load_rules(path=None):
    """Return {"default": str, "rules": [...]} from the rules JSON, or an empty
    ruleset if the file is missing, unreadable, not a dict, or malformed. Rule
    entries that are not dicts or lack a model are skipped."""
    path = path or DEFAULT_RULES
    empty = {"default": "", "rules": []}
    if not path or not os.path.exists(path):
        return empty
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return empty
    if not isinstance(data, dict):
        return empty
    default = data.get("default")
    default = default if isinstance(default, str) else ""
    rules = []
    raw = data.get("rules")
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict) or not entry.get("model"):
                sys.stderr.write(
                    "hearth-router: skipping invalid rule: {!r}\n".format(
                        entry.get("name") if isinstance(entry, dict) else entry))
                continue
            rules.append(entry)
    return {"default": default, "rules": rules}


def _matches(rule, goal_lower, tools):
    """Pure: does this rule match the (lowercased) goal or the tools list?
    A rule matches when ANY of its any_keywords is a case-insensitive substring
    of the goal, OR any of its tools_any is present in tools."""
    for kw in rule.get("any_keywords") or []:
        if kw and str(kw).lower() in goal_lower:
            return True
    wanted = rule.get("tools_any") or []
    if wanted and tools:
        have = set(tools)
        for t in wanted:
            if t in have:
                return True
    return False


def choose_model(goal, tools=None, rules=None, fallback=None):
    """Return the model that should run this goal. Evaluate the rules in order;
    the first one that matches (by keyword or by tool) wins and returns its
    model. With no match, return rules["default"] if set, else the caller's
    fallback, else "". Pure and deterministic."""
    if rules is None:
        rules = load_rules()
    goal_lower = (goal or "").lower()
    toollist = _tools_list(tools)
    for rule in rules.get("rules") or []:
        if _matches(rule, goal_lower, toollist):
            return rule.get("model")
    default = rules.get("default")
    if default:
        return default
    if fallback:
        return fallback
    return ""


def explain(goal, tools, rules):
    """Return {"chosen", "matched_rule", "why"} describing the selection, for
    observability and logging."""
    goal_lower = (goal or "").lower()
    toollist = _tools_list(tools)
    for rule in rules.get("rules") or []:
        if _matches(rule, goal_lower, toollist):
            return {"chosen": rule.get("model"),
                    "matched_rule": rule.get("name"),
                    "why": "matched rule {!r}".format(rule.get("name"))}
    default = rules.get("default")
    if default:
        return {"chosen": default, "matched_rule": None,
                "why": "no rule matched; used default"}
    return {"chosen": "", "matched_rule": None,
            "why": "no rule matched and no default set"}


def _self_test():
    import tempfile
    d = tempfile.mkdtemp(prefix="hearth-router-")
    cfg = os.path.join(d, "router.json")
    with open(cfg, "w") as fh:
        json.dump({
            "default": "llama3.2:3b",
            "rules": [
                {"name": "code",
                 "any_keywords": ["code", "refactor", "python", "bug",
                                  "compile", "function"],
                 "tools_any": ["edit_file", "replace_in_files"],
                 "model": "qwen2.5-coder:latest"},
                {"name": "research",
                 "any_keywords": ["research", "summarize", "analyze", "compare"],
                 "model": "qwen2.5:7b-instruct"},
                {"name": "bad-no-model",
                 "any_keywords": ["whatever"]},
            ],
        }, fh)

    rules = load_rules(cfg)
    # the malformed entry (no model) is skipped
    assert [r["name"] for r in rules["rules"]] == ["code", "research"], rules
    assert rules["default"] == "llama3.2:3b", rules

    # a code-ish goal picks the coder model by keyword
    assert choose_model("please refactor this Python function", rules=rules) \
        == "qwen2.5-coder:latest"
    # ... and separately by tools_any, even with an unrelated goal
    assert choose_model("do the thing", tools=["read_file", "edit_file"],
                        rules=rules) == "qwen2.5-coder:latest"
    # tools as a comma string is normalized the same way
    assert choose_model("do the thing", tools="read_file,replace_in_files",
                        rules=rules) == "qwen2.5-coder:latest"
    # matching is case-insensitive
    assert choose_model("BIG REFACTOR", rules=rules) == "qwen2.5-coder:latest"

    # a research goal picks the instruct model
    assert choose_model("summarize and compare these papers", rules=rules) \
        == "qwen2.5:7b-instruct"

    # an unmatched goal falls back to the declared default
    assert choose_model("say hello", rules=rules) == "llama3.2:3b"

    # first matching rule wins (code before research)
    assert choose_model("research this code", rules=rules) \
        == "qwen2.5-coder:latest"

    # explain() reports the matched rule and the default path
    e = explain("refactor the parser", None, rules)
    assert e["chosen"] == "qwen2.5-coder:latest" and e["matched_rule"] == "code", e
    e2 = explain("say hello", None, rules)
    assert e2["chosen"] == "llama3.2:3b" and e2["matched_rule"] is None, e2

    # an empty ruleset: no default, so choose_model returns the caller's fallback
    empty = {"default": "", "rules": []}
    assert choose_model("anything", rules=empty, fallback="mistral:7b") \
        == "mistral:7b"
    # ... and "" when there is no fallback either
    assert choose_model("anything", rules=empty) == ""
    e3 = explain("anything", None, empty)
    assert e3["chosen"] == "" and e3["matched_rule"] is None, e3

    # a missing config file is a safe no-op empty ruleset
    missing = os.path.join(d, "does-not-exist.json")
    assert load_rules(missing) == {"default": "", "rules": []}

    # HEARTH_ROUTER pointing at a missing file: still a safe no-op
    saved = os.environ.pop("HEARTH_ROUTER", None)
    try:
        os.environ["HEARTH_ROUTER"] = missing
        assert load_rules() == {"default": "", "rules": []}
        # with no rules and no default, choose_model returns the fallback
        assert choose_model("hi", rules=load_rules(), fallback="llama3.2:3b") \
            == "llama3.2:3b"
        # invalid JSON also reads as empty
        badf = os.path.join(d, "bad.json")
        with open(badf, "w") as fh:
            fh.write("{not json")
        assert load_rules(badf) == {"default": "", "rules": []}
        # a non-dict top level reads as empty
        listf = os.path.join(d, "list.json")
        with open(listf, "w") as fh:
            json.dump(["nope"], fh)
        assert load_rules(listf) == {"default": "", "rules": []}
    finally:
        if saved is None:
            os.environ.pop("HEARTH_ROUTER", None)
        else:
            os.environ["HEARTH_ROUTER"] = saved

    print("hearth-router self-test OK")
    return 0


def main(argv=None):
    p = argparse.ArgumentParser(prog="hearth-router")
    p.add_argument("--rules", default=DEFAULT_RULES,
                   help="path to the rules JSON")
    p.add_argument("--self-test", action="store_true")
    sub = p.add_subparsers(dest="cmd")

    pc = sub.add_parser("choose", help="print the model chosen for a goal")
    pc.add_argument("--goal", default="")
    pc.add_argument("--tools", default="")
    pc.add_argument("--fallback", default="")

    sub.add_parser("list", help="print the loaded rules")

    a = p.parse_args(argv)
    if a.self_test:
        return _self_test()

    rules = load_rules(a.rules)
    if a.cmd == "choose":
        print(choose_model(a.goal, tools=a.tools, rules=rules,
                           fallback=a.fallback or None))
        return 0
    if a.cmd == "list":
        print("default: {}".format(rules["default"] or "(none)"))
        for r in rules["rules"]:
            print("{}  keywords={}  tools_any={}  model={}".format(
                r.get("name"), r.get("any_keywords") or [],
                r.get("tools_any") or [], r.get("model")))
        return 0
    p.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
