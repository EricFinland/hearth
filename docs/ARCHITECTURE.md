# hearth Architecture

## 1. System diagram

```
  +-------------------+        git push         +----------------------------+
  |   MacBook Air     |  ---------------------> |   git remote (GitHub)      |
  |   (Apple Silicon) |                         +----------------------------+
  |   editor only     |                                     |
  |   nix develop     |                                     | git pull / flake ref
  +-------------------+                                     v
                                          +-------------------------------------+
                                          |   Proxmox VM: hearth-workstation    |
                                          |   x86_64-linux, GTX 1660 Ti, 32G    |
                                          |                                     |
                                          |   nixos-rebuild switch --flake .    |
                                          |                                     |
                                          |   modules:                          |
                                          |     base          (users, ssh)      |
                                          |     llm           (ollama + cuda)   |
                                          |     agents        (runtimes, dirs)  |
                                          |     sandbox       (systemd isolation)|
                                          |     observability (audit, sqlite)   |
                                          |     networking    (tailscale, fw)   |
                                          |     shell         (status, prompt)  |
                                          |     mcp           (audit gate)      |
                                          +-------------------------------------+
```

The Mac is the editor. It builds nothing of consequence locally beyond flake
evaluation. The Proxmox VM is the build and run target.

## 2. Module responsibilities

- base.nix: locale and timezone, the non-root `hearth` service user, SSH
  hardening (no passwords, no root login), base CLI tooling, the systemd-boot
  EFI bootloader, and the firewall switch.

- llm.nix: the Ollama service with CUDA acceleration for the GTX 1660 Ti, a
  declarative model manifest (`hearth.llm.models`), and a oneshot service that
  pulls the declared models on activation. Model storage is redirected to
  /var/lib/hearth/models.

- agents.nix: the /var/lib/hearth directory layout via tmpfiles, the base agent
  runtimes (Python with uv, Node.js LTS), and the sops-nix integration stub for
  secret material.

- sandbox.nix: the reusable least-privilege systemd profile that agent services
  merge in. This is the core isolation mechanism. See the threat model below.

- observability.nix: the audit daemon (currently a stub), the SQLite run store
  schema, the `hearth-runs` query command, and persistent journald with a 2G
  cap.

- networking.nix: Tailscale for mesh access, a firewall that trusts the
  Tailscale interface and opens only SSH and the local Ollama port.

- shell.nix: the login message, the `hearth-status` command, and interactive
  shell tooling (starship, fzf, zoxide, btop).

- mcp.nix: the MCP audit gate. A declared MCP server with `auditRequired = true`
  cannot start until an approval file exists. Stub now, real binary later.

## 3. Threat model

What the sandbox protects against:

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

What the sandbox does NOT protect against:

- A compromised Nix store. If the store is tampered with, every derived service
  is suspect. Integrity of the store is assumed.
- A kernel exploit. The syscall filter narrows the surface but a kernel zero-day
  defeats userspace isolation.
- A malicious NixOS module. Anything you import into the flake runs with full
  build and activation privileges. Review modules before importing them.
- Network exfiltration. `PrivateNetwork=false` is set because agents need
  outbound access. Per-agent network isolation is a roadmap item (Day 4), not a
  current guarantee.

## 4. Dev and test loop

```
edit on Mac  ->  git push  ->  ssh hearth-workstation
             ->  cd /path/to/hearth  (or reference the flake URL)
             ->  sudo nixos-rebuild switch --flake .#workstation
             ->  hearth-status   # verify services
             ->  test the change
```

Rollback is one command: `sudo nixos-rebuild switch --rollback`. Every switch is
a new generation in the bootloader.

## 5. Image build pipeline

```
nixos-generators  --format qcow  --flake .#workstation
        |
        v
   result-image/nixos.qcow2
        |
        v
   import into Proxmox as a VM disk  (qm importdisk)
        |
        v
   boot, ssh in, nixos-rebuild switch for subsequent updates
```

The image build is for the first boot. After that, updates flow through
`nixos-rebuild switch` against the repo, not by rebuilding images.
