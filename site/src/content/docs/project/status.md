---
title: Project status
description: A snapshot briefing of what is built, what is stubbed, and what needs input.
---

Read this first. It is the morning briefing for the hearth scaffold.

## What was built

A complete NixOS flake scaffold for hearth, a security-first host for local LLMs
and sandboxed agents. The repo has a working flake.nix (nixpkgs unstable,
nixos-generators, sops-nix, home-manager), eight NixOS modules (base, llm,
agents, sandbox, observability, networking, shell, mcp), a concrete
workstation host profile, the core docs, build/deploy/bootstrap scripts, a
GitHub Actions flake-check workflow, a TUI dashboard spec, and the README.

## Decisions made (the three non-obvious ones)

- NixOS flake over bootc (OCI images). Reproducibility and atomic, bootloader
  level rollback won. bootc stays a documented pivot. See
  [Decision records](/hearth/project/decisions/) ADR-002.
- sops-nix over agenix for secrets. sops-nix supports age, PGP, and KMS keys and
  has broader adoption; agenix is age-only. See
  [Decision records](/hearth/project/decisions/) ADR-003.
- Textual (Python) for the boot dashboard, not bubbletea (Go). Python is already
  in the agent runtime, so no new language toolchain.

## What is stubbed or needs your input

Be aware before you rely on anything here:

- sops-nix key setup is not done. You must run `age-keygen -o
  ~/.config/sops/age/keys.txt`, put the public key in a `.sops.yaml`
  creation rule, and create real secrets. The repo only ships a placeholder at
  /etc/hearth/sops.yaml. Walkthrough in
  [Decision records](/hearth/project/decisions/) ADR-003.
- The hearth-audit daemon is a shell-script stub. It prints a start line and
  sleeps. It does not yet read agent logs or write rows into the SQLite store.
  The schema is documented in nixos/modules/observability.nix.
- The MCP audit binary does not exist yet. nixos/modules/mcp.nix ships a gate
  that blocks any auditRequired MCP server without an approval file, but the real
  scanner that produces approvals is a roadmap item (Day 6).
- GPU passthrough requires Proxmox-side setup. The GTX 1660 Ti must be passed
  through to the VM over PCIe before CUDA works. See
  https://pve.proxmox.com/wiki/PCI_Passthrough

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
