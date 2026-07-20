---
title: Project status
description: A snapshot briefing of what is built, what is stubbed, and what needs input.
---

Read this first. It is the morning briefing for hearth.

**Current release: v1.5.0 (Governor).** The stack runs on real hardware. Local
model quality is the honest ceiling; everything below is built and shipped
unless this page says otherwise. For what comes next, see the
[Roadmap](/hearth/project/roadmap/).

## What was built

A declarative NixOS system for hearth, a security-first host for local LLMs and
sandboxed agents. The repo has a working flake.nix (nixpkgs unstable,
nixos-generators, sops-nix, home-manager), the NixOS modules (base, llm,
agents, sandbox, observability, networking, shell, mcp, egress, tripwire), a
concrete workstation host profile, the agent runtime, build/deploy/bootstrap
scripts, a GitHub Actions flake-check workflow, and the full docs.

The shipped capability set at v1.5:

- **Sandboxed agents** as ephemeral systemd units (no writes outside their
  workspace, no host secrets, no privilege escalation), with permission modes
  (plan / auto / bypass), an approvals queue, and a kill switch.
- **Every run audited** to local SQLite (tokens, cost, latency, errors), plus a
  live cloud-cost-saved counter.
- **Reproducible** whole-OS flake with atomic, bootloader-level rollback.
- **OpenAI-compatible API**, a knowledge base (RAG) with semantic retrieval, a
  standing-missions scheduler, and a human-gated self-improvement loop.
- **Per-run containment**: capability manifests (tool allowlists) and egress
  allowlists you declare at launch. See
  [Per-run containment](/hearth/concepts/per-run-containment/).
- **Honeyfile tripwires** that catch a run reaching for planted credentials
  (v1.2).
- **The flight recorder and replay viewer** (v1.3): every run records a
  structured per-step event stream, and a scrubber replays it step by step.
  Plus **run diff** (v1.3), a side-by-side prompt comparison across two local
  models. See [Replay](/hearth/operations/replay/).
- **OS-level egress enforcement** (v1.4): declared hosts are enforced with
  per-run nftables rules at the kernel, not just in the web tools.
- **The governor** (v1.5): a spend circuit breaker (a hard daily token cap),
  unified alerting to Telegram and ntfy, and declarative cron-as-flake
  missions. See [The governor](/hearth/operations/governor/).

## Decisions made (the three non-obvious ones)

- NixOS flake over bootc (OCI images). Reproducibility and atomic, bootloader
  level rollback won. bootc stays a documented pivot. See
  [Decision records](/hearth/project/decisions/) ADR-002.
- sops-nix over agenix for secrets. sops-nix supports age, PGP, and KMS keys and
  has broader adoption; agenix is age-only. See
  [Decision records](/hearth/project/decisions/) ADR-003.
- Textual (Python) for the boot dashboard, not bubbletea (Go). Python is already
  in the agent runtime, so no new language toolchain.

## What needs your input, and what is still roadmap

Be aware before you rely on anything here:

- sops-nix key setup is not done for you. You must run `age-keygen -o
  ~/.config/sops/age/keys.txt`, put the public key in a `.sops.yaml`
  creation rule, and create real secrets. The repo only ships a placeholder at
  /etc/hearth/sops.yaml. Walkthrough in
  [Decision records](/hearth/project/decisions/) ADR-003.
- OS-level egress enforcement is off by default. It goes up only when you set
  `hearth.egress.enable = true` and a run declares `allowed_hosts`; the tool
  layer enforces regardless. See
  [Per-run containment](/hearth/concepts/per-run-containment/).
- GPU passthrough requires Proxmox-side setup. The GPU must be passed through
  to the VM over PCIe before CUDA works. See
  https://pve.proxmox.com/wiki/PCI_Passthrough
- Some layers are deliberately roadmap, not shipped: kernel-level `auditd`
  watch rules for raw-open tripwire detection and signed export of replay
  recordings both land in v2.0. See the [Roadmap](/hearth/project/roadmap/) for
  the full list.

## Where to run things

The `nix` commands run on a Mac or a NixOS VM, not on Windows. Windows has no
`nix`. Use the Windows checkout for editing and git only; do the actual
evaluation and image build on the Mac or a NixOS host.

```
# On your Mac / NixOS machine (not Windows):
nix flake check
bash scripts/build-image.sh
```

For the full operational sequence, see the [Runbook](/hearth/operations/runbook/).

## GitHub

The repo lives at https://github.com/EricFinland/hearth.
