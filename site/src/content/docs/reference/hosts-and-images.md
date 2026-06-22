---
title: Hosts & images
description: The flake's host configurations and image build targets, and when to use each.
---

The flake defines three host configurations and two image build targets. They
share the same modules, so a host and the image built from it stay identical.

## Host configurations

Build or apply these with `nixos-rebuild switch --flake .#<name>`.

### `workstation`

The full deployable host: every module with the LLM stack (Ollama + CUDA)
enabled. This is the Day-2-and-beyond target for the Proxmox VM.

```sh
sudo nixos-rebuild switch --flake .#workstation
```

### `workstation-minimal`

The same host with `hearth.llm.enable = false`. It skips compiling CUDA, so the
first image builds in minutes instead of waiting on the CUDA stack. Use it to
prove the boot and deploy loop, then switch to `.#workstation` once the VM is up.

### `blade`

A concrete bare-metal host: a Razer Blade 15 laptop (Intel iGPU + NVIDIA RTX
2060, WiFi). It is built and installed directly on the machine, not imaged, with
both `hearth.llm.enable` and `hearth.gpu.enable` turned on. It serves as the
reference for real-hardware GPU inference.

## Image build targets

Build these with `nix build .#<name>` (or use `scripts/build-image.sh`). Both use
the `qcow-efi` format, which pairs with the systemd-boot EFI setup, so build the
Proxmox VM with OVMF (UEFI).

### `image-minimal`

The lean first-boot image (built from `workstation-minimal`). No CUDA, fast.

```sh
nix build .#image-minimal
# or
bash scripts/build-image.sh
```

### `image`

The full image with the Ollama + CUDA stack (built from `workstation`).

```sh
nix build .#image
# or
bash scripts/build-image.sh image
```

## Which do I use?

| Goal | Use |
| --- | --- |
| First boot, prove the pipeline fast | `image-minimal` |
| Full LLM host on a VM | `image` then switch to `.#workstation` |
| Bare-metal laptop with a real GPU | `.#blade` |
| Day-to-day updates after first boot | `nixos-rebuild switch --flake .#workstation` |

The image build is only for the first boot. After that, updates flow through
`nixos-rebuild switch` against the repo, not by rebuilding images. See the
[Runbook](/hearth/operations/runbook/) for the full sequence.
