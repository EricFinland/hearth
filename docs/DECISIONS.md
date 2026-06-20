# hearth Decision Records

Architecture decision records for the choices that shaped hearth. Each records
the context, the decision, the rationale, and the consequences.

## ADR-001: NixOS over a from-scratch kernel or a remastered ISO

### Context

hearth needs a Linux system whose entire state is defined in one place and can
be rebuilt the same way every time. The candidates were: build from scratch
(custom kernel and userland), remaster an existing distro (for example an Ubuntu
respin with a package overlay), or use NixOS configured by a flake.

### Decision

Use NixOS configured by a flake. The flake is the single source of truth and the
lock file pins every input.

### Rationale

- Reproducibility. The flake lock pins nixpkgs and every other input to exact
  revisions. Two builds from the same lock produce the same system.
- Atomic rollback. `nixos-rebuild switch` creates a new generation. If it breaks,
  `nixos-rebuild --rollback` returns to the previous one. The bootloader lists
  generations, so recovery does not depend on a working userland.
- A clean upgrade path. Day to day, the system advances with
  `nixos-rebuild switch --flake .#workstation`, not by rebuilding images.
- Remastering a distro would require maintaining a custom package overlay and
  still lacks atomic rollback. State drift is the normal failure mode of a
  respin.
- A from-scratch kernel is out of scope for a one-week prototype and buys nothing
  the project needs.

### Consequences

- The Nix language has a real learning curve. Module authors must understand the
  options system and the module merge model.
- In exchange, the system is legible from boot: every service, package, and file
  traces back to a module in this repo.

## ADR-002: bootc was not chosen

### Context

bootc (bootable OCI containers) is an alternative way to ship an immutable,
image-based Linux system. It was a credible candidate against the NixOS flake
approach.

### Decision

Use the NixOS flake. Keep bootc as a documented pivot if Nix complexity becomes
a blocker.

### Comparison

| Criterion | NixOS flake | bootc (OCI image) |
|---|---|---|
| Reproducibility | Nix lock file, byte-for-byte | OCI layers, less deterministic |
| Rollback | nixos-rebuild --rollback | bootc rollback |
| Ecosystem maturity | Established, large community | Emerging as of 2024 |
| GPU/driver support | Nixpkgs has CUDA overlays | Depends on base image |
| Learning curve | High (Nix language) | Moderate (Containerfile) |
| Pivot cost | Medium | Low |

### Rationale

NixOS wins on the two properties hearth values most: byte-for-byte
reproducibility and atomic, bootloader-level rollback. CUDA support through
nixpkgs overlays matters directly for the GTX 1660 Ti. bootc's lower learning
curve and lower pivot cost are real, but they do not outweigh reproducibility for
a system whose whole thesis is legible, rebuildable state.

### Consequences

- We accept the steeper Nix learning curve.
- bootc remains a valid pivot. The module boundaries in this repo (one concern
  per module) would survive a move to a Containerfile-based build with moderate
  rework.

## ADR-003: sops-nix over agenix

### Context

Secrets (API keys, tokens) must not live in plaintext in the repo. The two
common NixOS options are sops-nix and agenix.

### Decision

Use sops-nix.

### Rationale

- sops-nix supports multiple key types: age, PGP, and cloud KMS (for example AWS
  KMS). agenix is age-only.
- sops-nix has broader community adoption and integrates with existing sops
  tooling that teams may already use.
- agenix is simpler, but the simplicity costs flexibility for multi-key
  scenarios.

For a homelab with one developer, either tool works. sops-nix is chosen for
forward compatibility: if hearth later needs a second key type or a KMS-backed
key, no migration is required.

### Consequences

- Slightly more configuration up front (a .sops.yaml creation-rules block).
- Setup steps for the maintainer:
  1. Generate an age key: `age-keygen -o ~/.config/sops/age/keys.txt`
  2. Note the public key printed by that command.
  3. Create `.sops.yaml` at the repo root with a creation rule mapping
     `secrets/.*\.yaml$` to that public key.
  4. Create secrets with `sops secrets/example.yaml`.
  5. Reference them in NixOS with `sops.secrets.<name>` and target the decrypted
     output at /var/lib/hearth/secrets (mode 0700).
