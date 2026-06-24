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
12 iterations so a confused model cannot run forever. Local models sometimes emit
tool calls as JSON inside their text rather than in the structured field, so the
loop parses tool calls from the message content too.

Every step emits runtime state (so the [map](/hearth/operations/map-dashboard/)
can show the agent thinking) and the whole run is recorded to the audit database
like any other run. See [Observability & audit](/hearth/concepts/observability/).

## The tools

Tools live in a pluggable registry (`agent/hearth_tools.py`). Each tool has a
name, a description, a JSON schema for its parameters, and a function that takes
`(args, workspace)`. The built-in tools are:

| Tool | What it does |
| --- | --- |
| `run_command` | Run a shell command in the workspace (build, test, inspect). Times out after a limit. |
| `write_file` | Create or overwrite a file in the workspace. |
| `read_file` | Read a file from the workspace. |
| `list_files` | List files in a workspace directory. |
| `http_request` | Make an HTTP request to an external API (url, method, headers, body). |

Adding a capability is adding one entry to the registry, so the surface an agent
can touch is explicit and reviewable.

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

```
command center  ->  /var/lib/hearth/queue/<id>.json
                ->  hearth-spawn (path watcher)
                ->  hearth-agent@<id>  (sandboxed)
                ->  hearth-loop in /var/lib/hearth/agents/<id>
                ->  audited run + live map state
```

:::note[On the roadmap]
A Claude-Code-style control layer (permission modes and live, resumable sessions)
is specified and planned but not yet built. Today a run executes to completion
under its sandbox; interactive approval of individual tool calls is the next step.
:::
