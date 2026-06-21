---
title: Roadmap
description: The day-by-day build plan and what is done versus pending.
---

This is the working task list. Check items off as you go.

## Day 1: Foundation (de-risk the loop)

- [x] flake.nix evaluates, `nix flake check` passes (verified in CI with `--no-build`, 2026-06-20)
- [x] base.nix builds a minimal bootable NixOS config (`.#image-minimal` built green in CI, 2026-06-20)
- [ ] produce an image and boot it as a Proxmox VM (image build verified in CI; booting needs your Proxmox node, see the [Runbook](/hearth/operations/runbook/))
- [ ] confirm ssh in and `nixos-rebuild switch` works against the repo (needs the booted VM, see the [Runbook](/hearth/operations/runbook/) Step 4 to 5)

Notes: items 1 and 2 are done and verified on real Nix in GitHub Actions. Items
3 and 4 are blocked only on hardware (a Proxmox node and SSH access), not on
code. Everything for them is in place and documented in the
[Runbook](/hearth/operations/runbook/).

## Day 2: LLM layer

- [x] Ollama running as a systemd service on boot (configured in modules/llm.nix; starts Ollama on boot in the full image)
- [x] declarative model manifest: list models in config, they get pulled on activation (hearth.llm.models + hearth-model-pull service)
- [ ] GPU passthrough working for the 1660 Ti, verify a model runs on the GPU (needs the Proxmox node and the card, see the [Runbook](/hearth/operations/runbook/))

Notes: the LLM config is code-complete. The model storage path under
/var/lib/hearth/models and the CUDA build still need a runtime check on the real
GPU; nothing else here is blocked.

## Day 3: Agent runtime

- [x] agent framework installed and launchable (hearth-agent runner on PATH; audit logic unit-tested locally, evaluates green in CI)
- [x] defined agent home layout under /var/lib/hearth (tmpfiles in modules/agents.nix)
- [ ] secrets handling working (no plaintext keys in the repo) (sops-nix wired, .sops.yaml + secrets/example.yaml in place; needs your age key to actually encrypt, see [Decision records](/hearth/project/decisions/) ADR-003)

Notes: the runner is real Python (agent/hearth_agent.py), standard library only,
and its audit path passes a local self-test. A live model run needs Ollama and a
pulled model on the VM.

## Day 4: Sandbox and observability (the differentiators)

- [x] agents run under least privilege (hearth-demo-agent runs under the sandbox profile; hearth-sandbox-selftest probes the boundaries)
- [x] every agent run logs cost, tokens, latency, errors to the local store (hearth-agent writes a row to SQLite and a JSON record per run)
- [x] one query that shows the last N runs with their stats (hearth-runs)

Notes: the code is complete and evaluates green. The actual proof output (the
self-test journal showing each denied/allowed probe) must be captured on the
booted VM. Stronger isolation (per-agent network, bind-mount filesystem allow
list) is captured as a stretch item below.

## Day 5: Shell and dashboard

- [x] login boots into a TUI showing system state, running agents, model status, recent runs, spend (dashboard/hearth_dashboard.py, Textual; auto-launches on login via modules/shell.nix)

Notes: data layer unit-tested locally, the Textual UI passed a headless smoke
test against Textual 8.2.7, and the whole config builds into the image in CI.
The live panels (unit states, model list) show real values only on the booted
VM; off-target they degrade to placeholders.

## Day 6: Packaging

- [ ] build-image.sh produces a clean distributable image
- [ ] bootstrap.sh applies the config to a fresh NixOS host in one command
- [ ] polish first-boot experience

## Day 7: Demo and decision

- [ ] record a short demo, write the [Demo](/hearth/operations/demo/) page
- [ ] clean the README
- [ ] decide: ship publicly or keep private
