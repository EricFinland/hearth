---
title: Command reference
description: The commands hearth puts on PATH and the services it installs.
---

hearth installs a small set of commands and systemd services. This is the
reference for what each one does.

## Interactive commands

These are on `PATH` after a rebuild.

### `hearth-status`

System overview: Ollama state, Tailscale state, and recent runs. The first thing
to run after you SSH in.

```sh
hearth-status
```

### `hearth-runs`

Prints the most recent agent runs from the audit database with their tokens,
cost, and latency. See [Observability & audit](/hearth/concepts/observability/).

```sh
hearth-runs
```

### `hearth-agent`

The agent runner. Calls a local Ollama model, times it, and records the run to
SQLite plus a per-run JSON record.

```sh
# run an agent against a local model
hearth-agent --agent-name demo --model llama3.2:3b "Reply with a five word greeting."

# verify the audit path without Ollama
hearth-agent --self-test

# create or migrate the audit schema
hearth-agent --init-db
```

It is plain Python with no third-party dependencies, so the audit path and
`--self-test` work even where Ollama is not running.

### `hearth-loop`

The tool-using agent loop. Give a model a goal and a workspace; it calls tools
(run commands, read and write files, make HTTP requests) until the goal is done
or it hits the iteration cap. The run is sandboxed and audited. See
[Agent engine](/hearth/concepts/agent-engine/).

```sh
hearth-loop --model qwen2.5-coder --agent-name builder --workspace DIR "GOAL"

# run the loop against a mock model, no Ollama needed
hearth-loop --self-test
```

### `hearth-dashboard`

A Textual TUI showing system state, model status, spend, and recent runs. It
auto-launches on interactive login. Force the plain-text version with `--plain`,
or check the data layer with `--self-test`.

```sh
hearth-dashboard           # the TUI
hearth-dashboard --plain   # text fallback, no TUI
```

Set `HEARTH_NO_DASHBOARD=1` to suppress the auto-launch on login.

## Services

Start these with `systemctl`; read their output with `journalctl`.

### `hearth-demo-agent`

A demo agent run executed under the sandbox profile and recorded to the audit
store. The packaged, sandboxed equivalent of running `hearth-agent` by hand.

```sh
sudo systemctl start hearth-demo-agent
journalctl -u hearth-demo-agent --no-pager
```

### `hearth-sandbox-selftest`

Runs under the same profile as a real agent and probes each isolation boundary,
reporting what is allowed and what is denied. See
[Sandboxing & threat model](/hearth/concepts/sandboxing/).

```sh
sudo systemctl start hearth-sandbox-selftest
journalctl -u hearth-sandbox-selftest --no-pager
```

### `hearth-audit-init`

A boot oneshot that initializes the audit database schema by calling
`hearth-agent --init-db`. You do not normally run it by hand; it runs on
activation so the schema exists before any agent does.
