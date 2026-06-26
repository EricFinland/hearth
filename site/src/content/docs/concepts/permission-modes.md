---
title: Permission modes & approvals
description: How hearth gates what an agent is allowed to do, the kill switch, and what runs unsandboxed.
---

Every tool an agent wants to call passes through a permission decision before it
runs. The decision depends on the run's **mode** and the tool's **risk class**.
This is what lets you hand an agent real reach without handing it a blank check.

## The three modes

Set the mode per run (`--mode`, or the mode selector in the cockpit).

| Mode | What it does |
| --- | --- |
| `plan` | Read-only. Only `safe` tools run; everything else is denied. The agent investigates and produces a plan, changing nothing. |
| `auto` | `safe` and `edit` tools run automatically; `dangerous` tools **pause for your approval**. The default. |
| `bypass` | Everything runs, no prompts. Use only when you are watching. |

## Risk classes

Each tool is classified once, in `agent/permissions.py`. An unknown tool is
treated as **dangerous** by default, so the system fails closed.

| Class | Tools |
| --- | --- |
| **safe** (read-only) | `read_file`, `list_files`, `current_generation`, `list_generations`, `system_health`, `read_self_config`, `git_status`, `git_diff`, `nix_check`, `remember`, `recall` |
| **edit** (writes files) | `write_file`, `write_self_config` |
| **dangerous** (shell, network) | `run_command`, `http_request`, `web_search`, `web_fetch` |

In `auto` mode you can also pre-approve specific shell command heads (an
`auto-allow` list), so for example `git` and `ls` run without a prompt while
anything else still gates.

## The approval flow

When a `dangerous` tool gates, the run pauses in the `WAITING_APPROVAL` state and
waits for a decision:

- **Interactive sessions** surface the request in the cockpit; you approve or deny
  it there.
- **Background workers** write the request to a `pending_actions` table and poll
  until it is decided.

Either way the decision comes through `POST /decide` (`{ id, allow }`). Approve
and the tool runs; deny and the model gets a "denied by user" result and carries
on. Pending requests are visible at `GET /pending`.

## Kill switch

One control stops everything: `POST /stop-all` (the stop-all button in the
cockpit). It stops every interactive session, stops every running
`hearth-agent@<id>` unit, and denies all outstanding pending approvals so nothing
is left blocked. It reports how many of each it cleared.

## What actually runs unsandboxed

There is an important nuance. The original demo agent runs under the full
[sandbox profile](/hearth/concepts/sandboxing/). But the **on-demand background
workers** launched from the cockpit run as the `operator` user with sudo
available, on purpose: you asked for an agent that can act on the real machine.

For those workers, containment is not the OS sandbox. It is three other things
working together:

- the **audit log** (every step recorded),
- the **approval queue** in `auto` mode (dangerous actions gate), and
- the **kill switch** (stop everything at once).

So choose the mode to match the trust. Investigate in `plan`, work in `auto` and
approve as you go, and reserve `bypass` for tasks you are actively supervising.
Per-run reach is scoped further by [credential allow-lists](/hearth/reference/agent-credentials/).
