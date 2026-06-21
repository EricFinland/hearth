---
title: Troubleshooting & FAQ
description: Common questions and the fixes for the problems people actually hit.
---

If something here does not cover your case, open an issue on
[the repository](https://github.com/EricFinland/hearth).

## FAQ

### Can I run hearth on Windows?

No. hearth is a NixOS system and every build/apply command uses `nix`, which does
not run on Windows. Use a Mac or a Linux/NixOS machine to build, and deploy to a
NixOS host or VM. New to this? Start with the
[Linux / NixOS primer](/hearth/installation/linux-primer/).

### Is hearth a Linux distro I flash onto a laptop?

No. It is a declarative NixOS configuration (a flake), not a custom kernel or a
remastered ISO. See [What is hearth](/hearth/getting-started/what-is-hearth/).

### Do I need a GPU?

No, but it is what makes local models fast. Without `hearth.gpu.enable`, Ollama
runs models on the CPU. See [GPU passthrough](/hearth/reference/gpu-passthrough/).

### How do I change which models are pulled?

Set [`hearth.llm.models`](/hearth/reference/configuration/#hearthllmmodels) and
rebuild. The pull is idempotent.

### How do I update after first boot?

You do not rebuild images. Edit the flake, push, and run
`sudo nixos-rebuild switch --flake .#workstation` on the host. See the
[Runbook](/hearth/operations/runbook/).

### How do I undo a bad change?

`sudo nixos-rebuild switch --rollback`, or pick a previous generation from the
bootloader. Every switch is a new generation.

## Troubleshooting

### The VM fails to mount root after a rebuild

The image sets disk labels at build time, and the VM hardware config expects root
labeled `nixos` and the EFI partition `ESP`. If they differ, check the real
labels and update `nixos/hosts/hardware-vm.nix` to match:

```sh
lsblk -o NAME,LABEL,FSTYPE
```

### `nixos-rebuild` fails with "unit already loaded"

A previous rebuild was interrupted and left a failed unit. Clear it, then switch
again:

```sh
sudo systemctl reset-failed nixos-rebuild-switch-to-configuration.service
sudo nixos-rebuild switch --flake .#workstation
```

### The model pull does nothing or errors about `$HOME`

The Ollama CLI panics if `HOME` is unset, and it reports the server active before
the socket is actually ready. hearth's `hearth-model-pull` service already sets
`HOME=/root` and waits for the server to answer before pulling. If you pull by
hand in a bare shell, set `HOME` first.

### `hearth-runs` shows nothing

The audit database has no rows until an agent has actually run. Trigger one:

```sh
sudo systemctl start hearth-demo-agent
hearth-runs
```

A run against a dead Ollama still records (with its error and latency), so an
empty result means no run has happened yet, not that recording is broken.

### Permission denied reading the audit database

The audit store is owned by the `hearth` user and group. To read it as the admin,
the `operator` account must be in the `hearth` group (it is, by default, via
`nixos/modules/admin.nix`). If you added another user, add it to the `hearth`
group too.

### CUDA takes forever to build

By default CUDA compiles for many GPU architectures. Pin it to your card's
compute capability so the build is a fraction of the size. See the
[GPU passthrough](/hearth/reference/gpu-passthrough/#the-cuda-compile-time-pin)
page.

### I cannot SSH in

SSH is key-only; password auth is disabled. Make sure your public key is in
[`hearth.adminKeys`](/hearth/reference/configuration/#hearthadminkeys) and that
the image was built after you added it. As a fallback, log in at the local or
hypervisor console as `operator` (initial password `hearth`).

### `nvidia-smi` does not list the card

On a VM, the GPU must be passed through from the host first. On bare metal,
`hearth.gpu.enable` must be true and the system rebuilt. See
[GPU passthrough](/hearth/reference/gpu-passthrough/).
