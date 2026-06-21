---
title: Quickstart
description: Clone hearth, validate the flake, and build an image.
---

This gets you from zero to a validated flake and a buildable image. It assumes a
machine with Nix and flakes enabled.

:::caution[You need Nix]
Every command here uses `nix`. Windows cannot run them directly. Use a Mac, a
Linux box, or a NixOS host. New to this? Start with the
[Linux / NixOS primer](/hearth/installation/linux-primer/).
:::

## 1. Clone

```sh
git clone https://github.com/EricFinland/hearth
cd hearth
```

## 2. Validate the flake

The first run fetches inputs and takes a few minutes.

```sh
nix flake check
```

## 3. Build a Proxmox-compatible image

```sh
bash scripts/build-image.sh
```

## 4. Apply to an existing NixOS host

```sh
bash scripts/bootstrap.sh
```

## Where to go next

- Deploying to a machine that already runs NixOS? See
  [Existing NixOS host](/hearth/installation/existing-nixos-host/).
- Starting from nothing? See
  [Fresh install (VM / Proxmox)](/hearth/installation/fresh-install/).
- For the full operational sequence on real hardware, see the
  [Runbook](/hearth/operations/runbook/).
