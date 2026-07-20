---
title: Features
description: What hearth does today, the differentiators, and what is captured for later.
---

## Core (required for a demo)

- Declarative NixOS system: the entire OS is defined in the flake. `nixos-rebuild switch` applies changes atomically.
- Ollama service: runs on boot, serves models on localhost:11434.
- Agent runtime: Python and Node.js environments available, agent working directories under /var/lib/hearth/agents.
- Sandboxed execution: agents run as ephemeral DynamicUser processes with ProtectSystem=strict and NoNewPrivileges.
- Run-level observability: every agent run records tokens, cost, latency, and errors to a local SQLite database.
- Boot dashboard: a TUI that shows system state, model status, and recent runs on login.
- Tool-using agent loop: `hearth-loop` gives a model a goal and tools (run commands, read and write files, HTTP, web search and fetch), runs it in a per-run workspace, and audits it. See [Agent engine](/hearth/concepts/agent-engine/).
- Permission modes with per-tool approval (plan, auto, bypass), a pending-approval queue, and a one-button kill switch. See [Permission modes](/hearth/concepts/permission-modes/).
- Autonomy modes: a swarm that decomposes and parallelizes, a marathon that loops until done, self-evolution gated on `nix flake check`, and an always-on growth daemon that compounds validated improvements. See [Autonomy](/hearth/concepts/autonomy/).
- Self-learning memory: agents record and recall lessons so the system gets smarter over time.
- OpenAI-compatible API: `/v1/chat/completions` with real token streaming and `/v1/models`, so any OpenAI client uses your local models, audited the same as any run.
- Knowledge base (RAG): semantic retrieval on local embeddings with a lexical fallback, auto-recalled into agent context, and whole-repo project indexing (`index_dir`).
- Standing missions scheduler: define a mission once and hearth dispatches it on a schedule, the works-while-you-sleep layer. See [Standing missions](/hearth/operations/scheduler/).
- Web command center and an animated [world view](/hearth/operations/world-view/): chat, launch agents, and watch them work in the browser.
- Optional KDE Plasma desktop and a fully-local [content toolchain](/hearth/reference/content-toolchain/) (ffmpeg, yt-dlp, TTS) for on-box media agents.

## Differentiators

- Least-privilege agent sandboxing with a written threat model (see [Sandboxing & threat model](/hearth/concepts/sandboxing/)). Agents cannot read host secrets or write outside their allowed paths by default.
- Per-run audit log: `hearth-runs` queries the SQLite store and prints the last 20 runs with cost and latency.
- Per-run capability manifests: a launch declares the exact tools a run may use, and an unlisted tool is a hard deny in every permission mode including bypass, enforced in the permission engine and filtered out of the model's tool list. See [Per-run containment](/hearth/concepts/per-run-containment/).
- Egress allowlists, enforced at two layers: a tool-layer host allowlist on the web tools, and OS-level nftables rules keyed on the run's cgroup that drop everything else at the kernel. Both layers log every attempt, allowed or blocked, to the same audit table. See [Per-run containment](/hearth/concepts/per-run-containment/).
- Honeyfile tripwires: every workspace is seeded with decoy secret files carrying unique canary tokens, so an agent reaching for credentials it was never asked to touch is flagged, killed by default, and recorded. See [Per-run containment](/hearth/concepts/per-run-containment/).
- Flight recorder and replay viewer: every run records a structured per-step event stream (tool call, args, output, duration, permission verdict), and a browser scrubber replays any past run step by step. See [Replay](/hearth/operations/replay/).
- Run diff: the same prompt against two local models side by side, comparing tokens, latency, and output. See [Replay](/hearth/operations/replay/).
- Spend circuit breaker: a hard daily token budget across all runs, checked against real audited usage before each model call. At the cap, running agents halt gracefully and new runs refuse to start. See [The governor](/hearth/operations/governor/).
- Unified alerting: push notifications to Telegram and ntfy on error, tripwire, and budget breach (successful completion opt-in), best-effort so a failed alert never blocks a run. See [The governor](/hearth/operations/governor/).
- Declarative scheduled missions (cron-as-flake): declare missions as `hearth.schedule.missions.<name>` in the flake, and each launch carries its own capability manifest and egress allowlist. See [Standing missions](/hearth/operations/scheduler/).
- Security scoreboard: `GET /security` and a cockpit panel showing what containment is active on the box right now (remote auth, rate limit, manifests, egress activity, tripwire status, breaker and alerting state, daemon health).
- Cloud cost saved: a live counter of what your runs would have cost on a frontier cloud model, in the world HUD and the cockpit stats panel.
- MCP audit gate: no MCP server is allowed to start until it has an approval file at /var/lib/hearth/mcp-audit/{name}.approved. Stub is in place; wiring it to a real scanner is a backlog item.
- Declarative model manifest: models are listed in the NixOS config and pulled automatically on activation.
- Full reproducibility: `nixos-rebuild switch --flake .#workstation` brings any NixOS host to the exact defined state.

## Stretch (captured, not built)

On the [roadmap](/hearth/project/roadmap/):

- auditd raw-open decoy detection: catch an agent that opens a bait file directly, without going through a tool (v2.0).
- Syscall anomaly baseline: record the syscall profile of normal runs and flag deviation, observe-and-alert rather than auto-kill (v2.0).
- Signed flight-recorder export: export any replay as a signed artifact, shareable proof of exactly what an agent did (v2.0).
- Declarative model router: rule-based model selection in the flake, a cheap model for easy tasks and escalation on failure (v1.6).
- Full tycoon map: agents as buildings, runs as workers, cost as resources, plus achievements and a system bill-of-materials page (v1.7).
- witness integration: a first-class NixOS module that ships local browser-agent observability and deep-links audit rows to its DOM-diff replay (v1.8).
- Deep research, audited: a research agent that fans out through the egress allowlist, pulls sources into the knowledge base, and synthesizes a cited report, every fetch in the egress log (v1.9).
- Multimodal vision: local vision models via Ollama so agents can read screenshots, charts, and PDFs (v2.1).

Unscheduled in [IDEAS.md](https://github.com/EricFinland/hearth/blob/main/IDEAS.md):

- Bind-mount filesystem jail: hide the wider filesystem behind an allow list instead of read-only-but-visible.
- Priority-queue workers: sandboxed workers pulling tasks from a queue, each with its own cgroup CPU and RAM cap.
- Boot attestation: TPM-measured boot plus a dashboard panel confirming the running system matches a known commit.
- Signed and attested images: cosign or sigstore proof that the booted image matches the exact flake commit.
