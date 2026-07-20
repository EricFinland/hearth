---
title: Replay & the flight recorder
description: Scrub through any past run step by step, and diff the same prompt across two models.
---

hearth has always recorded that a run happened: the audit row captures the
model, tokens, cost, latency, and errors. As of v1.3, it also records exactly
what the run did. Every run writes a structured per-step event stream, and a
replay viewer lets you drag a scrubber through any past run and watch it play
back. This deepens the audit guarantee: not just "an agent ran at 3am and cost
this much," but every tool call it made, what came back, how long it took, and
what the permission engine said about each one.

The recorder lives in the agent loop, `agent/hearth_loop.py`; the replay viewer
and its endpoints are served by `hearth-mapd` (port 8770), like the rest of the
cockpit.

## What the flight recorder captures

As a run executes, the agent loop appends one event per step to a new
`run_steps` table in the audit database. Each step carries:

- **seq**, the step's position in the run.
- **kind**, one of `think`, `tool`, `tripwire`, `done`, or `error`.
- **tool name**, which tool was called (for `tool` steps).
- **args**, the arguments passed to the tool, truncated to 2000 characters.
- **output**, what the tool returned, truncated to 4000 characters.
- **duration_ms**, how long the step took.
- **permission verdict**, what the permission engine decided for the call.

Recording is best-effort by design: if a write fails, the run keeps going. The
recorder observes runs; it is never a reason one fails.

To disable recording, set:

```bash
HEARTH_RECORDER=off
```

## The replay viewer

The viewer is served at:

```
http://<host-ip>:8770/replay
```

From there you can:

- **Pick a run.** Choose any past run that has recorded steps.
- **Scrub.** Drag the timeline through the run's steps at your own pace.
- **Watch.** A sprite acts out each step on a mini stage as you scrub, so the
  shape of the run (thinking, tool calls, errors, a tripwire trip) is visible
  at a glance.
- **Inspect.** Open any tool call to see its args, output, duration, and
  permission verdict.

The cockpit at `/command` has a replay card that takes you straight there.

### Endpoints

The viewer is backed by two endpoints you can also call directly:

- `GET /replay/agents` lists the runs that have recorded steps.
- `GET /replay/data?agent=<id>` returns the full step stream for one run.

## Run diff

The same release adds a two-model comparison. `POST /diff` runs the same prompt
against two local models and returns tokens, latency, and output side by side.
A run-diff card in the cockpit at `/command` renders the comparison, so "which
model should I use for this?" is a question you answer with a live test instead
of a guess.

A diff is not a side channel: both sides are recorded to the audit log under
the agent name `diff`, so every comparison you run leaves the same trail as any
other run.

## What's next

Signed export of recordings is planned for v2.0: export any replay as a signed
artifact, shareable proof of exactly what an agent did. See the
[roadmap](/hearth/project/roadmap/) for where it fits.

For the run-level layer of the audit story (the audit rows, Prometheus metrics,
and usage history), see
[Observability & audit](/hearth/concepts/observability/).
