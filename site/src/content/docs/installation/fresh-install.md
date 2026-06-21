---
title: Fresh install (VM / Proxmox)
description: Build a hearth image and boot it in a virtual machine or on bare metal.
---

Starting from nothing? Build an image from the flake and boot it. The primary
documented target is a Proxmox VM, but any hypervisor or bare-metal machine that
can boot a NixOS image works.

:::caution[Prerequisites]
- A machine with Nix and flakes to build the image (a Mac or Linux box). Windows
  cannot build it. See the [Quickstart](/hearth/getting-started/quickstart/).
- A Proxmox node or other hypervisor to run it.
- Your SSH public key (`ssh-keygen -t ed25519` if you do not have one).
:::

## 1. Add your SSH key before building

hearth disables SSH password auth, so bake your key in first. Edit
`nixos/hosts/workstation.nix`:

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

## 2. Build the image

```sh
cd hearth
bash scripts/build-image.sh
```

## 3. Boot it

Upload the built image to your hypervisor and boot it as a new VM. On Proxmox,
import the disk and attach it to a new VM. Until your key takes effect you can
reach the box through the Proxmox web console (user `operator`, initial password
`hearth`, which you should change with `passwd` on first login).

## 4. Apply updates over SSH

Once it boots and you can SSH in, future changes are just rebuilds against the
repo:

```sh
sudo nixos-rebuild switch --flake .#workstation
```

:::note[Full hardware walkthrough]
GPU passthrough, disk import, and the exact Proxmox steps are operational and
need real hardware. They are documented step by step in the
[Runbook](/hearth/operations/runbook/).
:::
