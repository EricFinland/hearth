---
title: Linux / NixOS primer
description: For newcomers. Get a NixOS machine ready so you can install hearth.
---

hearth is built on NixOS. If Linux is new to you, this page gets you to the
starting line. It is a map, not a full tutorial, with links to the official docs
at each step.

## What NixOS is

NixOS is a Linux distribution where the whole system is described by configuration
files instead of changed by hand. You declare what you want, run a rebuild, and
the system matches your declaration. If a change breaks something, you roll back
to a previous generation from the boot menu. That is exactly the property hearth
relies on.

## The path to running hearth

1. **Get a machine to run it on.** A spare laptop or desktop, or a VM on a
   hypervisor like Proxmox or VirtualBox. hearth assumes x86_64.
2. **Install NixOS.** Download the ISO and follow the official guide:
   [nixos.org/download](https://nixos.org/download) and the
   [NixOS manual installation guide](https://nixos.org/manual/nixos/stable/#sec-installation).
   During install, NixOS generates a hardware configuration for your machine.
3. **Enable flakes.** hearth is a flake. Enable the feature by adding this to your
   configuration and rebuilding:

   ```nix
   nix.settings.experimental-features = [ "nix-command" "flakes" ];
   ```

4. **Pick your path.** Now you have a NixOS machine. Continue with
   [Existing NixOS host](/hearth/installation/existing-nixos-host/), or build a
   dedicated image with [Fresh install](/hearth/installation/fresh-install/).

## Helpful references

- [Nix & NixOS official site](https://nixos.org)
- [The NixOS manual](https://nixos.org/manual/nixos/stable/)
- [nix.dev tutorials](https://nix.dev)

:::tip
You do not need to master Nix to try hearth. Get NixOS installed with flakes
enabled, then follow an install guide. You can learn the language as you go.
:::
