---
title: Roadmap
description: The staged release plan from v1.2 to v2.0.
---

hearth is at **v1.2** (see the [changelog](https://github.com/EricFinland/hearth/blob/main/CHANGELOG.md)
and the [status page](/hearth/project/status/)). This page is the staged plan to
v2.0, sequenced the same way the v0.x line marched to 1.0: each release is one
coherent story, ships something demoable, and deepens at least one of the three
guarantees (sandboxed by default, every run audited, reproducible from boot).

Two commitments already made in the changelog land exactly where promised:
OS-level egress enforcement in v1.4, and auditd raw-open decoy detection in v2.0.

## v1.3 "Replay" (spectacle core)

- **Flight recorder.** Every run already writes an audit row and a JSON record.
  This extends that to a full per-step event stream (tool call, arguments,
  output, tokens, timing), plus a scrubber timeline in the cockpit: drag through
  a past run and watch each tool call and its result. The world map replays the
  run by animating the agent sprite.
- **Run diff.** Same prompt, two models, side by side: tokens, cost, latency,
  output.

Why first: the recording is the foundation for later features (signed exports in
v2.0 need it to exist), and it is an instant demo.

## v1.4 "Wall" (the egress promise)

- **OS-level egress enforcement.** The tool-layer allowlist from v1.1 becomes
  real at the network layer: per-run nftables rules keyed on the run's cgroup
  and uid, resolved from `allowed_hosts` at launch. Blocked connections land in
  the existing `egress_log` table, so the cockpit view just gets richer. This
  closes the "a clever agent could shell out and curl" gap.
- The map shows live blocked-connection flashes, and the security scoreboard
  flips to "egress: OS-enforced".

## v1.5 "Governor" (governance)

- **Spend circuit breaker.** A daily token and cost cap enforced system-wide.
  When hit, agents pause and you get a push notification.
- **Declarative scheduled agents.** Cron-as-flake:
  `hearth.missions.<name> = { schedule, budget, tools, allowedHosts }`,
  composing the capability manifests and egress allowlists from v1.1.
- **Unified alerting.** Push on completion, error, budget breach, or tripwire.
  Telegram exists today; ntfy joins it.

## v1.6 "Router" (autonomy and brains)

- **Declarative model router.** Rule-based model selection declared in the
  flake: a cheap model for easy tasks, a coder model for code, escalation on
  failure.
- **Natural-language audit query.** "What did the demo agent do yesterday?"
  answered by a local model over the audit database. All local, and hearth
  eating its own dog food.

## v1.7 "Tycoon" (spectacle payoff)

- **Full tycoon-ification of the map.** Agents as buildings, runs as workers
  walking to them, cost as resources, the GPU and memory feed as a power-plant
  gauge, tripwires as facility alarms (shipped in v1.2), egress blocks as
  bounced couriers.
- **Achievements.** Gamified uptime, run counts, and savings versus cloud.
- **System Bill of Materials page.** Every package, model, and module with its
  pinned hash, auto-generated from the flake. Legibility as spectacle.

## v1.8 "Witness" (two projects, one story)

[witness](https://github.com/EricFinland/witness) is local-first observability
for browser agents: per-step DOM diffs, before-and-after screenshots, and every
LLM call with tokens, cost, and latency, all stored locally with zero
telemetry. hearth contains the agent; witness shows exactly what it did. Same
philosophy, one narrative.

- **`hearth.witness.enable`.** A first-class NixOS module that ships witness
  declaratively: the viewer served on the box, storage under
  `/var/lib/hearth/witness`, browser agents on the host recorded automatically.
- **Audit deep links.** The audit row for a browser-agent run links to its
  witness trace, so the cockpit receipt opens straight into the DOM-diff
  replay.
- **Cross-promotion.** A "hearth + witness" docs page here, and witness
  documents hearth as its natural home. Both repos advertise a real
  integration, not just words.

The v1.3 flight recorder treats witness's replay UX as its north star, so the
two recorders feel like siblings from day one.

## v2.0 "Sentinel" (the promise, kept)

The v2.0 headline: **contained, and provable.**

- **auditd raw-open decoy detection.** The layer documented in
  `nixos/modules/tripwire.nix`: catch an agent that opens bait files directly,
  without going through a tool.
- **Syscall anomaly baseline.** Record the syscall profile of normal runs and
  flag deviation. Ships as observe-and-alert, not auto-kill.
- **Signed flight-recorder export.** Export any replay as a signed artifact:
  shareable proof of exactly what an agent did.
- **hearth-doctor report card v2.** One command that live-probes every
  guarantee (sandbox escape attempts, an egress block, a tripwire trip, an
  audit write, a signed-export verify) and prints a green/red report card.

## Backlog

Unscheduled ideas live in
[IDEAS.md](https://github.com/EricFinland/hearth/blob/main/IDEAS.md): the
bind-mount filesystem jail, boot attestation, priority-queue workers,
environment snapshot and rollback, signed images, the mcp-audit gate
integration, and more. Items get promoted from there into a release when the
sequencing makes sense.

## The original build plan

The day-by-day plan that took hearth from an empty repo to a bootable system is
preserved in the git history of this page. The remaining hardware-bound items
(booting the image on a Proxmox node, GPU passthrough verification) are tracked
in the [Runbook](/hearth/operations/runbook/).
