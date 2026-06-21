---
title: Existing NixOS host
description: Apply hearth to a machine that already runs NixOS.
---

If your target machine already runs NixOS, applying hearth is a single rebuild
against this flake.

:::caution[Prerequisites]
- The machine runs NixOS with flakes enabled.
- You have a hardware configuration for it (NixOS generates one at install time).
- You have your SSH public key. hearth disables SSH password auth.
:::

## 1. Get the flake

Clone the repo onto the host, or reference it as a flake input from your own
configuration.

```sh
git clone https://github.com/EricFinland/hearth
cd hearth
```

## 2. Add your SSH key

Edit `nixos/hosts/workstation.nix` and add your public key so you keep access
after the rebuild:

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

## 3. Rebuild

Apply the `workstation` configuration:

```sh
sudo nixos-rebuild switch --flake .#workstation
```

This creates a new generation. If anything breaks, roll back with
`sudo nixos-rebuild switch --rollback` or pick a previous generation from the
bootloader.

## 4. Verify

```sh
hearth-status
```

You should see Ollama active and the recent-runs section.

:::note
The `workstation` host targets specific hardware (see
`nixos/hosts/workstation.nix`). For different hardware, copy that host file and
adjust the imports for your machine. The deep operational walkthrough lives in the
[Runbook](/hearth/operations/runbook/).
:::
