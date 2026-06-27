# Changelog

All notable changes to hearth. Versions follow semantic versioning; each is a
git tag and a GitHub release.

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
