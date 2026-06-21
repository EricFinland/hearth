---
title: Observability & audit
description: How every agent run is recorded and how to query the audit log.
---

Every agent run on hearth is recorded. There is no trust-by-default: if an agent
ran, there is a row for it.

## What gets recorded

For each run, hearth writes to a local SQLite database:

- token count
- cost
- latency
- errors

## Querying runs

`hearth-runs` reads the SQLite store and prints the most recent runs with their
cost and latency:

```sh
hearth-runs
```

The boot dashboard also surfaces recent runs the moment you log in, so the last
thing an agent did is visible without running a command.

## Where it fits

Observability is one of hearth's core guarantees alongside sandboxing. See
[Features](/hearth/concepts/features/) for the full list and
[Architecture](/hearth/concepts/architecture/) for where the audit store lives in
the system.
