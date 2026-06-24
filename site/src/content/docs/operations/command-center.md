---
title: Command center
description: The web cockpit for chatting with models and launching sandboxed agents.
---

The command center is hearth's web cockpit. From one page you can see live system
state, chat with a local model, launch a sandboxed agent, and watch runs happen
on the map. It is served by [`hearth-mapd`](/hearth/operations/map-dashboard/) at:

```
http://<host-ip>:8770/command
```

On the [desktop](/hearth/reference/desktop/) it opens in its own window from an
app launcher entry, and the `Meta+A` shortcut toggles it.

## What is on the page

| Panel | What it does |
| --- | --- |
| Stats | Live system readout (GPU and memory) from the `/stats` feed. |
| Chat | Talk to a local Ollama model. Each exchange is recorded as a run. |
| Launch | Start a sandboxed agent with a goal, model, and name. |
| Map | The agent map, showing current runs. |
| Activity | A feed of recent runs and state changes. |

## Chatting

The chat panel posts to `/chat`, which calls Ollama's chat API and records the
exchange to the audit store like any other run, so chats show up in
[`hearth-runs`](/hearth/operations/commands/#hearth-runs) and the activity feed.

## Launching an agent

The launch panel posts to `/run`, which atomically enqueues a launch request.
From there the [on-demand spawn](/hearth/concepts/agent-engine/#on-demand-spawn)
path takes over: a path-watcher starts a per-run sandboxed agent that runs the
[agent loop](/hearth/concepts/agent-engine/) in its own workspace. Nothing the
browser sends runs unsandboxed; the request only adds a file to a queue.

:::caution[Access]
The command center is part of the map server, so the same
[token auth](/hearth/operations/map-dashboard/#access-and-token-auth) applies:
open from localhost, but a remote browser needs the bearer token. Prefer reaching
it over Tailscale. See [Networking & remote access](/hearth/reference/networking/).
:::
