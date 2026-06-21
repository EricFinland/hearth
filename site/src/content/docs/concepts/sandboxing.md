---
title: Sandboxing & threat model
description: How hearth isolates agents and what the sandbox is designed to stop.
---

This builds on the [Architecture](/hearth/concepts/architecture/) overview. The
reusable least-privilege systemd profile lives in `modules/sandbox.nix`, and every
agent service merges it in.

## What the sandbox protects against

- A rogue or prompt-injected agent reading host secrets. `ProtectSystem=strict`
  makes the filesystem read-only outside an explicit allow list, `ProtectHome`
  hides user home directories, and decrypted secrets live in a 0700 directory
  owned by a different user than the `DynamicUser` the agent runs as.
- An agent writing outside its allowed paths. Only /var/lib/hearth/agents and
  /var/lib/hearth/runs are writable; everything else is read-only.
- An agent escalating privilege. `NoNewPrivileges=true`, an empty
  `CapabilityBoundingSet`, `RestrictNamespaces=true`, and a `SystemCallFilter`
  that drops privileged and mount syscalls together close the common local
  escalation paths.
- A runaway agent fouling shared temp state. `PrivateTmp=true` gives each agent
  its own /tmp.

## What the sandbox does NOT protect against

- A compromised Nix store. If the store is tampered with, every derived service
  is suspect. Integrity of the store is assumed.
- A kernel exploit. The syscall filter narrows the surface but a kernel zero-day
  defeats userspace isolation.
- A malicious NixOS module. Anything you import into the flake runs with full
  build and activation privileges. Review modules before importing them.
- Network exfiltration. `PrivateNetwork=false` is set because agents need
  outbound access. Per-agent network isolation is a roadmap item (Day 4), not a
  current guarantee.

## Proving it

The `hearth-sandbox-selftest` service runs under the same profile as a real agent
and reports each boundary probe. See the [Demo](/hearth/operations/demo/) for what
the self-test actually proves, line by line.
