---
title: Agent engine
description: The tool-using agent loop, the tool registry, and how runs are spawned and contained.
---

The agent engine is what runs a goal to completion. You give a model a goal and a
set of tools; it thinks, calls a tool, reads the result, and repeats until the
goal is done or it hits the iteration cap. It runs on Ollama's chat tool-calling,
records the run to the audit store, and emits live state for the map.

## The loop: `hearth-loop`

`hearth-loop` is the runner. It is plain Python (standard library only).

```sh
hearth-loop --model qwen2.5-coder --agent-name builder --workspace DIR "GOAL"

# run the loop against a mock model, no Ollama needed
hearth-loop --self-test
```

Each iteration: the model is given the goal, the available tools, and the results
so far. It either calls a tool or replies with a final summary. The loop caps at
12 iterations so a confused model cannot run forever.

The loop is built to tolerate the quirks of small local models. It parses tool
calls out of the message content (not just the structured field) and recovers
from common malformations like a trailing comma, so a slightly-off tool call is
not lost. When a tool result shows a recoverable failure (a missing package, a
command not on `PATH`), the loop appends a short, actionable hint so a weak model
can self-correct on the next turn instead of looping on the same mistake.

Every step emits runtime state (so the [map](/hearth/operations/map-dashboard/)
can show the agent thinking) and the whole run is recorded to the audit database
like any other run. See [Observability & audit](/hearth/concepts/observability/).

## The tools

Tools live in a pluggable registry (`agent/hearth_tools.py`). Each tool has a
name, a description, a JSON schema for its parameters, and a function that takes
`(args, workspace)`. Each is also assigned a
[risk class](/hearth/concepts/permission-modes/#risk-classes) that decides whether
it runs freely, needs approval, or is denied in a given mode.

| Tool | What it does | Risk |
| --- | --- | --- |
| `read_file` | Read a file from the workspace. | safe |
| `list_files` | List files in a workspace directory. | safe |
| `write_file` | Create or overwrite a file in the workspace. | edit |
| `run_command` | Run a shell command in the workspace (build, test, inspect). Times out after a limit. | dangerous |
| `http_request` | Make an HTTP request (url, method, headers, body); resolves `cred:` headers. | dangerous |
| `web_search` | Search the web (keyless DuckDuckGo) and return title, URL, and snippet. | dangerous |
| `web_fetch` | Fetch a URL and return it as readable text. | dangerous |
| `current_generation`, `list_generations`, `system_health` | Report the running NixOS generation, the generation list, and system health. | safe |
| `read_self_config`, `git_status`, `git_diff` | Read hearth's own config repo, its git status, and diffs. | safe |
| `nix_check` | Validate the config with `nix flake check --no-build` (no build, no activation). | safe |
| `write_self_config` | Write a file in hearth's config repo (for self-evolution). | edit |
| `remember`, `recall` | Write and retrieve lessons in long-term memory. | safe |

Adding a capability is adding one entry to the registry, so the surface an agent
can touch is explicit and reviewable. The self-knowledge and memory tools are what
make the [autonomy modes](/hearth/concepts/autonomy/) possible.

## Containment: the per-run workspace

Every file and command tool operates inside a per-run workspace and refuses any
path that escapes it. A tool call trying to write to `../evil` is rejected, not
followed. Combined with the [sandbox profile](/hearth/concepts/sandboxing/) that
the run executes under, the agent is contained at two layers: the tool layer
refuses to leave the workspace, and the OS layer refuses writes outside the
allow list.

For how `http_request` reaches credentials without ever seeing their values, see
[Agent credentials](/hearth/reference/agent-credentials/).

## On-demand spawn

Agents do not have to be started by hand. The [command center](/hearth/operations/command-center/)
can launch one: it drops a small JSON request into `/var/lib/hearth/queue`, a
systemd path-watcher (`hearth-spawn`) notices it, and starts a per-run sandboxed
instance (`hearth-agent@<id>`). That instance reads the request, runs
`hearth-loop` in a fresh workspace at `/var/lib/hearth/agents/<id>`, and removes
the request file. The queue directory is the only extra path the instance can
write, so a launch cannot reach anything else.

The launch path is self-healing: if the queue watcher ever falls into a failed
state (which would otherwise silently swallow launches), enqueuing a run clears
that state, processes the queue immediately, and re-arms the watcher for next
time, so a launch never gets dropped without a trace.

```
command center  ->  /var/lib/hearth/queue/<id>.json
                ->  hearth-spawn (path watcher)
                ->  hearth-agent@<id>  (sandboxed)
                ->  hearth-loop in /var/lib/hearth/agents/<id>
                ->  audited run + live map state
```

:::tip[Now built: permission modes and higher-order runs]
Runs are governed by [permission modes](/hearth/concepts/permission-modes/) (plan,
auto, bypass) with per-tool approval, and the engine supports interactive sessions
plus higher-order modes, swarm, marathon, self-evolve, and an always-on growth
loop. See [Autonomy & self-improvement](/hearth/concepts/autonomy/).
:::
