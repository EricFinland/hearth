---
title: Runbook
description: The operational steps that need real hardware to install and run hearth.
---

Operational steps that need real hardware (your Mac, a NixOS host, or the
Proxmox node). These are the parts of Day 1 that cannot be done from Windows.

## Prerequisites

- A machine with Nix and flakes enabled (your Mac, or a NixOS box). Windows
  cannot run any of the `nix` commands below.
- A Proxmox node reachable over the network.
- Your SSH public key. Generate one if you do not have it:
  `ssh-keygen -t ed25519 -C "you@laptop"` then read `~/.ssh/id_ed25519.pub`.

## Step 0: put your SSH key in the config

Edit `nixos/hosts/workstation.nix` and add your public key:

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

This must happen before you build the image, because SSH password
authentication is disabled. Without a key baked in, you can only reach the box
through the Proxmox web console (user `operator`, initial password `hearth`,
which you should change with `passwd` on first login).

## Step 1: validate the flake

```
cd hearth
nix flake check --no-build
```

`--no-build` evaluates everything without compiling. This is the fast gate and
is exactly what CI runs. A plain `nix flake check` would also try to build the
full system, including CUDA, which is slow; skip that unless you mean it.

## Step 2: build the image

```
bash scripts/build-image.sh            # builds .#image-minimal (no CUDA, fast)
bash scripts/build-image.sh image      # builds .#image (full Ollama + CUDA)
```

Build the minimal image first. It boots in minutes and proves the pipeline
before you spend time compiling the CUDA stack. The output is a qcow2 file under
`result-image/`.

## Step 3: import the image into Proxmox

Copy the qcow2 to the Proxmox node and import it, or use the helper:

```
export PROXMOX_HOST=192.168.1.x
export PROXMOX_VMID=900
bash scripts/deploy.sh
```

Then in the Proxmox UI, for VM 900:

1. Set BIOS to OVMF (UEFI). The image uses systemd-boot, which needs UEFI.
2. Add an EFI disk (Proxmox requires one for OVMF).
3. Attach the imported disk and set it as the boot disk.
4. Set machine type q35, give it 4+ GB RAM and 2+ cores for the minimal image.
5. Start the VM and open the console.

## Step 4: first boot and login

- At the Proxmox console, log in as `operator` / `hearth` and run `passwd` to
  change the password.
- Find the VM IP (the QEMU guest agent reports it in the Proxmox summary), then
  from your laptop: `ssh operator@<vm-ip>`.
- Confirm services: `hearth-status`.

## Step 5: the update loop (this is the real workflow)

After first boot you do not rebuild images for changes. You edit the flake, push,
and switch on the VM:

```
# on the VM (or remotely)
sudo nixos-rebuild switch --flake github:EricFinland/hearth#workstation
# or, from a local checkout on the VM:
sudo nixos-rebuild switch --flake .#workstation
```

To roll back a bad change: `sudo nixos-rebuild switch --rollback`.

## Note on filesystem labels

The image format sets the disk labels when it builds. `nixos/hosts/hardware-vm.nix`
assumes root is labeled `nixos` and the EFI partition `ESP`. If the booted VM
fails to mount root after a `nixos-rebuild`, check the real labels with
`lsblk -o NAME,LABEL,FSTYPE` and update hardware-vm.nix to match, or regenerate
with `nixos-generate-config --show-hardware-config`.
