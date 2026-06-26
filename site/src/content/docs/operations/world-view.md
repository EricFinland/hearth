---
title: World view
description: The animated facility cockpit where agents are characters you can watch and direct.
---

The world view is hearth's default cockpit. Instead of a list of rows, your agents
are characters in a small top-down facility you can watch in real time. It is
served at:

```
http://<host-ip>:8770/world
```

On the [desktop](/hearth/reference/desktop/) it opens automatically at login in a
dedicated window, and `Meta+A` toggles a fullscreen view.

## What you see

- **Agents as sprites.** Each running agent is a character. Managers, specialists,
  and the growth daemon look different (the growth daemon carries a spinning gear).
  Their appearance is driven only by the agent's real runtime state, never by model
  output: thinking, running a tool, waiting on I/O, waiting for your approval,
  errored, or done.
- **Crew rooms and tethers.** A manager and its specialists are grouped into a
  labelled room (the room shows the mission goal), with dashed lines tethering
  children to their parent. Ungrouped agents sit in a free-agent zone.
- **A HUD** across the top with live GPU, VRAM, and RAM bars and an agent count.
- **A minimap** when the facility gets busy.
- **CRT effects** (scanlines, vignette, a subtle flicker) you can toggle.

## What you can do

- **Launch from the goal bar** at the bottom: type a goal and start a mission
  (swarm), a single agent, or the growth loop.
- **Click an agent** to open an inspect panel with its details and a scrollable
  transcript of its recent steps.
- **Open the ledger panel** for the [self-improvement](/hearth/concepts/autonomy/#the-self-improvement-ledger)
  view: the growth daemon's status, validated and merged counts, the lessons log,
  and the promote controls (review diff, build-check, apply to live, roll back).

## How it stays live

The page streams agent state changes over server-sent events (`/events`) and polls
`/tree`, `/state`, and `/stats` for lineage, current state, and system load. The
result is that a mission visibly plays out: a manager spawns a room of
specialists, each lights up as it thinks and runs tools, and the room resolves as
they finish.

The [command center](/hearth/operations/command-center/) remains available at
`/command` as a denser, panel-based control surface for the same actions. For the
underlying HTTP surface, see [Map dashboard](/hearth/operations/map-dashboard/).
