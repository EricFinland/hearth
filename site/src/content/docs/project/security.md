---
title: Security model
description: What hearth defends, the sharp edges to know about, and how to report a vulnerability.
---

hearth runs local language models and autonomous agents on your own machine. Its
whole point is to make that safer than the usual "give an agent full privileges
and hope." This page is the honest version: what it defends, where the sharp
edges are, and how to report a problem. The canonical policy is
[SECURITY.md](https://github.com/EricFinland/hearth/blob/main/SECURITY.md) in the
repository.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Instead:

- Open a private report through GitHub Security Advisories ("Report a
  vulnerability" on the repository's Security tab), the preferred channel, or
- Reach out through the [contact form](https://ericcatalano.dev/#contact) with
  details and, if possible, a reproduction.

You will get an acknowledgement, and once a fix ships the advisory will credit
you unless you ask otherwise.

## What hearth defends

- **Agents are contained at the OS level.** Background and demo agents run as
  hardened systemd units. The honest guarantee is: no writes outside the agent's
  own workspace, no reads of `/root`, `/home`, or the secrets directory, and no
  privilege escalation. It does not hide world-readable files like `/etc/passwd`;
  `ProtectSystem` makes the filesystem read-only, not invisible. See
  [Sandboxing & threat model](/hearth/concepts/sandboxing/).
- **Secrets are delivered by name, not value.** Stored credentials live in a
  sops-encrypted file and reach an agent through systemd's credential channel.
  Tools substitute `cred:NAME` references at request time, so the model never
  sees the raw secret, and a per-run allow-list scopes which credentials a run may
  resolve. See [Agent credentials](/hearth/reference/agent-credentials/).
- **Every run is audited.** Tokens, cost, latency, model, and errors are written
  to a local database, so there is always a record of what an agent did. See
  [Observability & audit](/hearth/concepts/observability/).
- **The whole OS is reproducible and reversible.** It is one flake; every change
  is an atomic generation you can roll back at the bootloader.

## Sharp edges to know about

This is the part most projects leave out. Read it before you run agents with real
reach.

- **Full-machine agents are opt-in and powerful.** Interactive sessions and
  background workers launched from the cockpit intentionally run as the `operator`
  user with `sudo` available. Containment there is the
  [audit log, the approval queue, and the kill switch](/hearth/concepts/permission-modes/),
  not the OS sandbox. Only expose the cockpit on a network you trust.
- **Default console password.** The first local console login uses a well-known
  `initialPassword`. SSH is key-only and remote password auth is disabled, but you
  should still change the console password immediately (`passwd`) or set your own
  `hashedPassword` before building.
- **The cockpit is token-gated, not hardened.** Localhost is open; remote access
  needs a bearer token. Treat it as a trusted-LAN tool, ideally reached over
  Tailscale rather than the open internet. See
  [Networking & remote access](/hearth/reference/networking/).
- **Local model quality is the real ceiling.** Self-evolve and growth changes are
  gated by `nix flake check` and only ever produced on isolated branches for human
  review. They are never auto-merged into the running system. See
  [Autonomy & self-improvement](/hearth/concepts/autonomy/).

## Supported versions

hearth is a young project under active development. Security fixes land on `main`.
Pin a specific commit and read the changelog before updating.
