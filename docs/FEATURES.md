# hearth Feature List

## Core (required for a demo)

- Declarative NixOS system: the entire OS is defined in the flake. `nixos-rebuild switch` applies changes atomically.
- Ollama service: runs on boot, serves models on localhost:11434.
- Agent runtime: Python and Node.js environments available, agent working directories under /var/lib/hearth/agents.
- Sandboxed execution: agents run as ephemeral DynamicUser processes with ProtectSystem=strict and NoNewPrivileges.
- Run-level observability: every agent run records tokens, cost, latency, and errors to a local SQLite database.
- Boot dashboard: a TUI that shows system state, model status, and recent runs on login.

## Differentiators

- Least-privilege agent sandboxing with a written threat model (see docs/ARCHITECTURE.md). Agents cannot read host secrets or write outside their allowed paths by default.
- Per-run audit log: `hearth-runs` queries the SQLite store and prints the last 20 runs with cost and latency.
- MCP audit gate: no MCP server is allowed to start until it has an approval file at /var/lib/hearth/mcp-audit/{name}.approved. Stub is in place; real binary is a roadmap item.
- Declarative model manifest: models are listed in the NixOS config and pulled automatically on activation.
- Full reproducibility: `nixos-rebuild switch --flake .#workstation` brings any NixOS host to the exact defined state.

## Stretch (captured, not built)

- OS-level token and cost budget enforcement that kills runaway agents when they exceed a configured limit.
- ntfy and Telegram alerting baked in for agent completion and errors.
- Tailscale auto-join to a homelab mesh with pre-shared keys declared in the flake.
- A "replay" view of a past agent run, showing each tool call and its output.
- Signed and attested images using cosign or nix-sigstore.
- A minimal web dashboard mirroring the TUI, accessible over Tailscale.
- Multi-agent scheduling with priority queues and resource limits.
- Snapshot and rollback of the entire agent environment (models, secrets, working state) as a Nix closure.
