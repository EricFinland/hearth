# hearth boot dashboard (TUI)

Status: Not yet implemented. Scheduled for Day 5 of the roadmap.

This document is the spec for the login dashboard. No code lives here yet.

## 1. Purpose

The dashboard is what a user sees on login. It answers, at a glance:

- System state: which hearth services are up (Ollama, audit daemon, Tailscale).
- Running agents: how many agent units are active right now.
- Model status: which models are pulled and whether Ollama is serving them.
- Recent runs: the last several agent runs with cost and latency.
- Current spend: cumulative cost_usd from the run store, for example today and
  this month.

It replaces the plain `hearth-status` text output with a live, refreshing view.

## 2. Proposed tool

Textual (Python).

Justification: Python is already in the agent runtime stack (see
nixos/modules/agents.nix), so the dashboard adds no new language toolchain.
Textual gives async refresh, widgets, and tables without a heavy dependency
tree. bubbletea (Go) is a fine alternative and would produce a single static
binary, but it would introduce Go as a second runtime for one component.

## 3. Data sources

- systemd units (via `systemctl is-active` or the D-Bus API):
  - ollama.service
  - hearth-audit.service
  - tailscaled.service
  - hearth-model-pull.service (oneshot, for last pull result)
  - any active agent units
- SQLite run store at /var/lib/hearth/runs/audit.db, table agent_runs:
  id, agent_name, run_id, started_at, finished_at, tokens_in, tokens_out,
  cost_usd, latency_ms, error, model
- Ollama API at http://localhost:11434/api/tags for pulled models.
- Tailscale status via `tailscale status --json` (BackendState, peers).

## 4. Layout mockup

```
+--------------------------------------------------------------------+
|  hearth                                   hearth-workstation  09:14 |
+----------------------+---------------------------------------------+
|  SYSTEM              |  RECENT RUNS                                 |
|  ollama      [up]    |  time      agent      model     cost  ms    |
|  audit       [up]    |  09:12     summarize  llama3.2  0.00  840   |
|  tailscale   [up]    |  09:05     classify   mistral   0.00  1320  |
|                      |  08:51     extract    llama3.2  0.00  610   |
|  MODELS              |  08:40     research   mistral   0.01  2950  |
|  llama3.2:3b [ok]    |                                             |
|  mistral:7b  [ok]    |  SPEND                                       |
|                      |  today     $0.01                             |
|  AGENTS              |  month     $0.37                             |
|  running     0       |                                             |
+----------------------+---------------------------------------------+
|  q quit   r refresh   enter: run detail                            |
+--------------------------------------------------------------------+
```

Numbers above are illustrative placeholders for layout only, not measured
results.

## 5. Status

Implemented (2026-06-20). The app is dashboard/hearth_dashboard.py, built on
Textual, packaged as the `hearth-dashboard` command in nixos/modules/shell.nix.

How it runs:
- It launches automatically on an interactive login shell (the programs.bash
  hook in modules/shell.nix). Press `q` to quit back to the shell. Set
  `HEARTH_NO_DASHBOARD=1` to skip the auto-launch.
- `hearth-dashboard --plain` prints a one-shot text snapshot. This is also the
  automatic fallback if the terminal cannot host a TUI.
- `hearth-dashboard --self-test` exercises the data layer against a temp
  database.

What is verified vs pending:
- The data layer (SQLite recent runs and spend) passes a local self-test.
- The Textual UI passes a headless smoke test (compose, mount, refresh, the runs
  table populates, the refresh and quit actions work) against Textual 8.2.7.
- The live system panels (systemd unit states, Ollama model list, running agent
  count) degrade to safe placeholders off-target and need the booted VM to show
  real values.

