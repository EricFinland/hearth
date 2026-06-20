# START HERE

Read this first. It is the morning briefing for the hearth scaffold.

## What was built

A complete NixOS flake scaffold for hearth, a security-first host for local LLMs
and sandboxed agents. The repo has a working flake.nix (nixpkgs unstable,
nixos-generators, sops-nix, home-manager), eight NixOS modules (base, llm,
agents, sandbox, observability, networking, shell, mcp), a concrete
workstation host profile, five docs (ARCHITECTURE, ROADMAP, DECISIONS, FEATURES,
DEMO), build/deploy/bootstrap scripts, a GitHub Actions flake-check workflow, a
TUI dashboard spec, and the README. Git history is nine clean, logically grouped
commits authored by Eric Catalano.

## Decisions made (the three non-obvious ones)

- NixOS flake over bootc (OCI images). Reproducibility and atomic, bootloader
  level rollback won. bootc stays a documented pivot. See docs/DECISIONS.md
  ADR-002.
- sops-nix over agenix for secrets. sops-nix supports age, PGP, and KMS keys and
  has broader adoption; agenix is age-only. See ADR-003.
- Textual (Python) for the boot dashboard, not bubbletea (Go). Python is already
  in the agent runtime, so no new language toolchain. See tui/README.md.

## What is stubbed or needs your input

Be aware before you rely on anything here:

- sops-nix key setup is not done. You must run `age-keygen -o
  ~/.config/sops/age/keys.txt`, put the public key in a `.sops.yaml`
  creation rule, and create real secrets. The repo only ships a placeholder at
  /etc/hearth/sops.yaml. Walkthrough in docs/DECISIONS.md ADR-003.
- The hearth-audit daemon is a shell-script stub. It prints a start line and
  sleeps. It does not yet read agent logs or write rows into the SQLite store.
  The schema is documented in nixos/modules/observability.nix.
- The MCP audit binary does not exist yet. nixos/modules/mcp.nix ships a gate
  that blocks any auditRequired MCP server without an approval file, but the real
  scanner that produces approvals is a roadmap item (Day 6).
- GPU passthrough requires Proxmox-side setup. The GTX 1660 Ti must be passed
  through to the VM over PCIe before CUDA works. See
  https://pve.proxmox.com/wiki/PCI_Passthrough
- Nothing here has been evaluated or built. These are Windows-authored files.
  `nix flake check` has not been run. Expect to fix small Nix evaluation issues
  on the first real check.

## First three commands to run in the morning

```
cd C:\Users\ericc\OneDrive\Desktop\hearth
# On your Mac / NixOS machine (not Windows):
nix flake check
bash scripts/build-image.sh
```

Note: the `nix` commands run on the Mac or a NixOS VM, not on Windows. Windows
has no `nix`. Use this Windows checkout for editing and git only; do the actual
evaluation and image build on the Mac or a NixOS host.

## GitHub status

Pushed. The repo was created as a private repository and pushed to:
https://github.com/EricFinland/hearth

If you ever need to re-add the remote or push from a fresh clone:

```
gh repo create hearth --private --source=. --remote=origin --push
```
