# Changelog

All notable changes to hearth. Versions follow semantic versioning; each is a
git tag and a GitHub release.

## v1.2.0 - Tripwire

Honeyfile decoys that catch an agent reaching for credentials, rendered as a
facility alarm.

- **Honeyfile decoys**: every agent workspace is seeded with a few convincing
  fake secrets files (`.aws/credentials`, `.env.production`, `secrets/api_keys.txt`),
  each embedding a unique canary token. A well-behaved agent never reads
  unrequested credentials; one that does trips the alarm.
- **Two detection layers**: a direct read of a decoy by path (caught before the
  contents ever reach the model), and a canary token surfacing in any tool's
  output (catches a shell `cat`, `grep`, etc. via `run_command`).
- **On a trip**: the run is flagged and, by default, killed; a row is written to
  a new `tripwires` audit table; the agent enters a new `TRIPPED` state; and a
  Telegram DM is sent if configured. `HEARTH_TRIPWIRE=flag` records and warns
  without killing; `HEARTH_DECOYS=off` disables planting.
- **System decoys**: `nixos/modules/tripwire.nix` plants world-readable bait
  under `/var/lib/hearth/decoys` and a sudo-only decoy inside the secrets dir,
  for an agent that goes looking beyond its workspace (raw-open detection via
  auditd arrives in v2.0).
- **Spectacle**: the world map flashes red with an alarm banner when any agent
  trips, the sprite shows a pulsing siren, and the cockpit security scoreboard
  goes armed with a live trip count. New `GET /tripwires`.

## v1.1.0 - Manifest

Per-run containment you can declare at launch, and the first spectacle counter.

- **Capability manifests**: a launch can declare `tools: [...]` and the run may
  use ONLY those tools, in every permission mode including bypass. Enforced in
  the permission engine (an unlisted tool is a hard deny), filtered out of the
  model's advertised tool list, and excluded from text-emitted tool-call parsing,
  so there is no path around the cap. Available on background runs, interactive
  sessions, missions, marathons, and self-evolve, via the cockpit or the API
  (`--allowed-tools` / `HEARTH_ALLOWED_TOOLS`).
- **Egress allowlists (tool layer)**: a launch can declare `allowed_hosts` and
  the web tools (`web_fetch`, `web_search`, `http_request`, `fetch_to_kb`) may
  reach ONLY those hosts (subdomains included, loopback always allowed). Every
  outbound attempt, allowed or blocked, is recorded to a new `egress_log` audit
  table, readable at `GET /egress`. OS-level enforcement lands in v1.4; this
  layer stops accidents and naive injection and makes all egress visible.
- **Swarm scoping inheritance**: specialists spawned by a mission manager now
  inherit the manager's credential, tool, and host scoping (previously scoping
  did not propagate to children).
- **Cloud cost saved**: the audit log now shows what your runs would have cost
  on a frontier cloud model; a live counter in the world HUD and the cockpit
  stats panel.
- **Security scoreboard**: `GET /security` and a cockpit panel showing what
  containment is active right now (remote auth, rate limit, manifests, egress
  activity, tripwire status, daemon health).
- New endpoints: `GET /tools` (the registry with risk classes), `GET /egress`,
  `GET /security`.

## v1.0.0 - Stable

First stable release. hearth is a declarative NixOS system for running local LLMs
and autonomous agents: sandboxed by default, every run audited, the whole OS
reproducible and reversible from one flake. No API changes from v0.9; this marks
the surface as stable and ships a full changelog and refreshed docs.

The complete capability set at 1.0:

- **Sandboxed agents** as ephemeral systemd units (no writes outside their
  workspace, no host secrets, no privilege escalation), with permission modes
  (plan / auto / bypass), an approvals queue, and a kill switch.
- **Every run audited** to local SQLite (tokens, cost, latency, errors).
- **Reproducible** whole-OS flake with atomic, bootloader-level rollback.
- **OpenAI-compatible API** (`/v1/chat/completions` with real token streaming,
  `/v1/models`) so any OpenAI client uses your local models, audited.
- **Knowledge base (RAG)** with semantic (local embedding) retrieval and lexical
  fallback; auto-recalled into agent context; project indexing of a whole repo.
- **Standing missions** scheduler (the works-while-you-sleep layer).
- **Self-improvement**: an always-on growth loop that proposes, validates
  (`nix flake check`), compounds, and learns, producing reviewable branches with
  one-click human-gated promote-to-live and an auto-rollback watchdog.
- **Observability**: a Prometheus `/metrics` endpoint, a live + historical stats
  view, and `hearth-doctor` for a one-command health check.
- **Local + private** throughout: Ollama on your own GPU, nothing leaves the box.

## v0.9.0 - Ready

- `hearth-doctor` one-command health check (Ollama, audit DB, disk, services).
- Per-IP sliding-window rate limiting on the server's POST endpoints.
- Mobile-responsive command cockpit.

## v0.8.0 - Projects

- `index_dir`: index a directory of code/text into the knowledge base under
  `name/relpath`, so an agent can learn a whole codebase and search it.

## v0.7.0 - Insight

- Prometheus `/metrics` endpoint (runs, tokens, errors, per-model, daemon health).

## v0.6.0 - Toolsmith

- `replace_in_files` (multi-file exact find/replace).
- `fetch_to_kb` (fetch a web page into the knowledge base in one step).

## v0.5.0 - Understanding

- Semantic knowledge base: local Ollama embeddings with TF-IDF fallback,
  embeddings cached per chunk; embed model added to the declarative manifest.

## v0.4.0 - Recall

- Auto-recall: relevant knowledge-base chunks and memory lessons are injected
  into agent context automatically.
- Real token streaming for the OpenAI-compatible endpoint.

## v0.3.0 - Knowledge

- Local knowledge base (RAG) with `kb_add` / `kb_search` (TF-IDF retrieval).

## v0.2.0 - Plug In

- OpenAI-compatible API (`/v1/chat/completions`, `/v1/models`).
- Standing-missions scheduler.
- Power tools: `list_tree`, `search_files`, `edit_file`.
- Usage-over-time stats view in the cockpit.

## v0.1.0 - First public release

- Declarative NixOS system, Ollama on boot, the tool-using agent loop, the
  least-privilege sandbox with a written threat model, the per-run SQLite audit
  log, the web command center, agent credentials by name, the swarm and marathon
  runners, the self-improvement / self-evolve loop, and the animated cockpit.
