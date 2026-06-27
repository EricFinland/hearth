---
title: Autonomy & self-improvement
description: The run modes that let hearth decompose, persist, evolve itself, and compound improvements over time.
---

The [agent engine](/hearth/concepts/agent-engine/) runs a single goal to
completion. On top of it, hearth has higher-order modes: it can split work across
a team, grind on a goal until it is judged done, propose changes to its own
configuration, and run an always-on loop that compounds validated improvements.

Every mode runs under the [permission model](/hearth/concepts/permission-modes/)
and is recorded to the audit log.

## The run modes

Pick a mode with a `hearth-loop` flag, or with a flag on `POST /run` from the
cockpit.

| Mode | Flag | What it does |
| --- | --- | --- |
| Single run | (default) | One goal, one workspace, up to 12 iterations. |
| Session | `--session` | A long-lived interactive session you steer turn by turn. |
| Swarm | `--manager` | Decompose a goal, spawn specialists, synthesize their results. |
| Marathon | `--marathon` | Loop in rounds until the goal is judged complete. |
| Self-evolve | `--evolve` | Propose a config change, validate it, commit a branch. |
| Growth | `--grow` | An always-on loop that proposes, validates, and compounds improvements. |

## Swarm

A manager agent takes a goal and:

1. **Decomposes** it into 2 to 5 independent subtasks (one model call).
2. **Spawns a specialist** per subtask as its own sandboxed `hearth-agent@<id>`
   unit.
3. **Collects** each specialist's result once they all reach `DONE` or `ERRORED`.
4. **Synthesizes** a final answer from the goal plus all the results.

Lineage is recorded in an `agent_meta` table (each child's `parent_id`, `kind`,
and `goal`), which is what the cockpit's mission tree and the `/tree` endpoint
render: the manager at the root, its specialists as children, each with its live
state.

## Marathon

A single goal, worked in rounds until it is actually finished rather than until an
iteration counter runs out. After each round a judge model replies `DONE` or
`CONTINUE:` with the next step. It stops at `DONE` or at the round cap (default
30).

With `--checkin`, marathon pauses each round and waits for a
[Telegram](/hearth/reference/telegram/) reply so you can steer it from your phone:
reply to redirect it, or send "stop" to end it.

It also does not take the model's word for "done." If the goal names deliverable
files, marathon checks that those files actually exist and are non-empty before it
accepts a `DONE`. If any are missing it vetoes the completion and tells the model
exactly which files to produce, which stops a weaker local model from declaring
victory without the artifacts.

## Self-evolve

This is hearth editing its own configuration, safely.

1. It works on a fresh git branch of the config repo.
2. The model edits Nix files using the `read_self_config` and `write_self_config`
   tools, with relevant lessons recalled from memory folded into its prompt.
3. After each edit pass it runs a **`nix_check` gate**: `nix flake check --no-build`.
   This evaluates the whole system without building or activating it.
4. It loops until the check passes (up to 8 rounds), then commits the validated
   branch and sends a Telegram note.

:::caution[It never switches the live system]
Self-evolve only ever produces a committed, validated branch for review. It does
not build or activate anything on the running host. Going live is a separate,
deliberate step (see Promote below).
:::

## Growth daemon

The growth daemon is the always-on version of self-evolve, and it compounds.
Opt in with [`hearth.grow.enable`](/hearth/reference/configuration/#hearthgrowenable).
Each cycle it:

1. **Recalls** past lessons from memory.
2. **Proposes** one small, safe improvement to hearth's own code or config (a
   read-only tool, a doc, a minor option, a self-test). It is told to avoid
   networking, bootloader, SSH, secrets, and large rewrites.
3. **Implements and validates** it through the self-evolve flow (with the
   `nix flake check` gate).
4. **Compounds** it: a validated branch is merged into the grow repo's `main`
   only if `main` still passes the check afterward. If the combination breaks, the
   merge is reverted, so the baseline is never left broken.
5. **Records the outcome** as a lesson (success or failure) in the ledger, and
   prunes merged branches.

The daemon reseeds its working repo from the live config whenever that config
changes, so it always builds on current reality. It can send a Telegram note on
each merged improvement.

## The self-improvement ledger

Everything the growth loop learns is queryable at `GET /growth` and shown in the
cockpit's ledger panel: the daemon's status, how many improvements validated and
merged, the running list of lessons (successes and failures), and the validated
branches waiting to go live.

## Promote (and the rollback watchdog)

Growth produces a better config; **promote** is the deliberate step that puts it
on the live machine. From the ledger panel you can review the diff, build-check,
apply to live, or roll back (`POST /promote`, with diff/status/history endpoints).

Promotion is guarded. After a switch activates a new generation, a promote unit
that is independent of the web server and the network waits for services to
settle and verifies the units that guard access (SSH, NetworkManager, and the
map server itself). If any of them is down, it **auto-rolls-back** to the previous
generation. If all are up, it lets the growth daemon reseed on the new config.

## Memory

Lessons live in a `learnings` table in the audit database. The `remember` and
`recall` tools let any agent write an insight and pull relevant ones back later by
keyword. The growth daemon uses this to avoid repeating improvements that already
failed, which is what makes the loop get smarter rather than just busier.

## Self-knowledge tools

To reason about itself, an agent has read-only tools classified as
[safe](/hearth/concepts/permission-modes/#risk-classes): `current_generation`,
`list_generations`, `system_health`, `read_self_config`, `git_status`, and
`git_diff`. They let an agent see the running generation, the system's health, and
its own configuration and history before it proposes a change.
