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

The denser, panel-based surface lives at `/command`. The default cockpit is now
the visual [world view](/hearth/operations/world-view/) at `/world`; the command
center is the same controls in a more compact dashboard.

## What is on the page

| Panel | What it does |
| --- | --- |
| Stats | Live system readout (GPU and memory) from the `/stats` feed. |
| Chat | Talk to a local Ollama model. Each exchange is recorded as a run. |
| Launch | Start an agent: pick a name, model, mode, credential filter, and goal. |
| Agents / Missions | Live agents and the mission tree of managers and specialists. |
| Session console | When a session is open, live events with inline approve/deny. |
| Pending approvals | Tool calls waiting on your decision. |
| Activity | A feed of recent runs and state changes. |

## Launching an agent

The launch panel posts to `/run`. You choose a
[permission mode](/hearth/concepts/permission-modes/) (plan, auto, or bypass), an
optional [credential allow-list](/hearth/reference/agent-credentials/), and one of
the run types:

- **Open session** for an interactive run you steer turn by turn.
- **Run in background** for a one-shot background worker.
- **Launch mission** for a [swarm](/hearth/concepts/autonomy/#swarm).
- **Marathon** to [loop until done](/hearth/concepts/autonomy/#marathon), optionally
  with Telegram check-ins.
- **Self-evolve** or **grow hearth** for the
  [self-improvement](/hearth/concepts/autonomy/) modes.

## Approvals and the kill switch

In `auto` mode, a dangerous tool call pauses and surfaces in the session console
or the pending-approvals panel; you approve or deny it inline. The **stop-all**
control halts every session and worker and clears the queue at once. See
[Permission modes & approvals](/hearth/concepts/permission-modes/).

## Chatting

The chat panel posts to `/chat`, which calls Ollama's chat API and records the
exchange to the audit store like any other run, so chats show up in
[`hearth-runs`](/hearth/operations/commands/#hearth-runs) and the activity feed.

:::caution[Access]
The command center is part of the map server, so the same
[token auth](/hearth/operations/map-dashboard/#access-and-token-auth) applies:
open from localhost, but a remote browser needs the bearer token. Prefer reaching
it over Tailscale. See [Networking & remote access](/hearth/reference/networking/).
:::
