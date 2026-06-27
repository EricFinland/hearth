# Security Policy

hearth runs local language models and autonomous agents on your own machine. Its
whole design goal is to make that safer than the usual "give an agent full
privileges and hope" approach. This document explains the security model, the
known sharp edges, and how to report a problem.

## Reporting a vulnerability

Please do not open a public issue for a security problem.

- Open a private report via GitHub Security Advisories ("Report a vulnerability"
  on the Security tab), or
- Email eric.catalano925@gmail.com with details and, if possible, a way to
  reproduce.

You will get an acknowledgement, and once a fix is out the advisory will credit
you unless you ask otherwise.

## What hearth defends

- **Agents are contained at the OS level.** Background and demo agents run as
  hardened systemd units (`ProtectSystem=strict`, `ProtectHome`,
  `NoNewPrivileges`, empty capability set, a syscall allow-list, a per-run
  private temp). The honest guarantee is: no writes outside the agent's own
  workspace, no reads of `/root`, `/home`, or the secrets directory, no
  privilege escalation. It does NOT hide world-readable files such as
  `/etc/passwd`; `ProtectSystem` makes the filesystem read-only, not invisible.
- **Secrets are delivered by name, not value.** Stored credentials live in a
  sops-encrypted file and reach an agent through systemd's credential channel.
  Tools substitute `cred:NAME` references at request time, so the model never
  sees the raw secret. A per-run allow-list scopes which credentials a given run
  may resolve.
- **Every run is audited.** Tokens, cost, latency, model, and errors are written
  to a local SQLite database, so there is always a record of what an agent did.
- **The whole OS is reproducible and reversible.** It is one flake; every change
  is an atomic generation you can roll back at the bootloader.

## Sharp edges to know about

- **Full-machine agents are opt-in and powerful.** The interactive sessions and
  background workers launched from the web cockpit intentionally run as the
  `operator` user with `sudo` available (full-machine reach was a deliberate
  design choice). Containment there is the audit log, the approval queue (in
  `auto` mode), and the kill switch, NOT the OS sandbox. Only expose the cockpit
  on a network you trust.
- **Default console password.** `nixos/modules/admin.nix` sets a well-known
  `initialPassword` for the first local console login. SSH is key-only and
  remote password auth is disabled, but you should still change the console
  password immediately (`passwd`) or set your own `hashedPassword` before
  building.
- **The cockpit is bearer-token gated, not hardened.** Localhost is open;
  remote access requires a token. Treat it as a trusted-LAN tool, ideally
  reached over a private mesh (Tailscale) rather than the open internet.
- **Local model quality is the real ceiling.** Self-improvement and self-evolve
  changes are gated by `nix flake check` and are only ever produced on isolated
  branches for human review; they are never auto-merged into the running system.

## Supported versions

hearth is a young project under active development. Security fixes land on
`main`. Pin a specific commit and read the changelog before updating.
