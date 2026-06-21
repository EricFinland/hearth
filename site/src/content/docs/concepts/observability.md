---
title: Observability & audit
description: How every agent run is recorded and how to query the audit log.
---

Every agent run on hearth is recorded. There is no trust-by-default: if an agent
ran, there is a row for it.

## What gets recorded

For each run, the `hearth-agent` runner writes two things:

- A row in a local SQLite database at `/var/lib/hearth/runs/audit.db`.
- A per-run JSON record alongside it.

Each record captures:

- model
- token count
- cost
- latency
- errors

The schema lives in one place (the runner, `agent/hearth_agent.py`) so the
database and the records never drift. The `hearth-audit-init` service calls
`hearth-agent --init-db` on boot to create the schema.

## Querying runs

`hearth-runs` reads the SQLite store and prints the most recent runs with their
tokens, cost, and latency:

```sh
hearth-runs
```

A failed run still records: if a model is unreachable, the row captures the error
and the latency, so a silent failure leaves a trail.

## On the dashboard

The boot dashboard surfaces this data the moment you log in:

- a SPEND panel (today, this month, total) computed from the audit database
- a RECENT RUNS table

So the last thing an agent did is visible without running a command. See the
[Command reference](/hearth/operations/commands/) for `hearth-dashboard`.

## Where it fits

Observability is one of hearth's core guarantees alongside sandboxing. See
[Features](/hearth/concepts/features/) for the full list and
[Architecture](/hearth/concepts/architecture/) for where the audit store lives in
the system.
