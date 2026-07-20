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

Since v1.3 there is also a per-step layer: a flight recorder writes every step
of a run (each tool call with its args, output, duration, and permission
verdict) to a `run_steps` table in the same database, and a replay viewer lets
you scrub through any past run. See
[Replay & the flight recorder](/hearth/operations/replay/).

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

## Prometheus metrics

hearth's server (`hearth-mapd`, port 8770) exposes `GET /metrics` in Prometheus
exposition format, so you can scrape run activity into Prometheus or Grafana.

The metrics include:

- `hearth_runs_total` (counter): total agent runs recorded.
- `hearth_tokens_total` (counter): total tokens across all runs.
- `hearth_errors_total` (counter): total runs that recorded an error.
- `hearth_runs_by_model{model="..."}` (counter): runs broken down per model.
- `hearth_daemon_up{unit="..."}` (gauge): 1 if the unit is active, else 0, for
  units like `hearth-grow.service`, `hearth-mapd.service`, and
  `hearth-schedule.timer`.

A small slice of the text output looks like this:

```text
# HELP hearth_runs_total Total agent runs recorded.
# TYPE hearth_runs_total counter
hearth_runs_total 1284
hearth_tokens_total 9583120
hearth_errors_total 7
hearth_runs_by_model{model="llama3"} 902
hearth_runs_by_model{model="mistral"} 382
hearth_daemon_up{unit="hearth-mapd.service"} 1
hearth_daemon_up{unit="hearth-grow.service"} 1
hearth_daemon_up{unit="hearth-schedule.timer"} 1
```

Point Prometheus or Grafana at `http://<your-hearth>:8770/metrics`. A minimal
Prometheus scrape config:

```yaml
scrape_configs:
  - job_name: hearth
    static_configs:
      - targets: ["your-hearth:8770"]
```

## Usage over time

The server also exposes `GET /stats/history`, which aggregates the audit log by
day and by model (runs, tokens, cost) plus grand totals. It is the same audit
data, rolled up so you can see where activity and spend went over time.

The command center renders this as a bar chart in the "usage over time" panel, so
trends in activity and spend are visible at a glance instead of buried in a list
of individual runs.

## Health check: hearth-doctor

`hearth-doctor` runs a one-shot health check of the install and prints a
pass/warn/fail checklist. It verifies:

- Ollama is reachable (and how many models are pulled).
- The audit database is present and writable.
- Disk space is healthy.
- The key services are active (`hearth-mapd`, the growth daemon, and the
  scheduler timer).

A run looks like this:

```text
hearth-doctor

OK    ollama reachable (3 models pulled)
OK    audit db present and writable
WARN  disk space at 82% on /var/lib/hearth
OK    hearth-mapd.service active
FAIL  hearth-grow.service inactive
OK    hearth-schedule.timer active

overall: FAIL
```

It exits non-zero if anything failed, so it is usable in scripts and CI checks.
When everything passes, the footer reads `overall: OK` and the exit code is 0.

## Where it fits

Observability is one of hearth's core guarantees alongside sandboxing. See
[Features](/hearth/concepts/features/) for the full list and
[Architecture](/hearth/concepts/architecture/) for where the audit store lives in
the system.
