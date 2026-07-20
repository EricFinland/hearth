---
title: What is hearth
description: What hearth is, what it is not, and who it is for.
---

hearth is a declarative NixOS configuration for running local language models and
autonomous agents on hardware you control. The entire operating system is defined
in one `flake.nix` that Nix builds reproducibly and deploys to any NixOS host or
Proxmox VM.

## What it is not

hearth is not a custom Linux kernel and not a remastered distro. There is no ISO
to flash with a bespoke userland. It is a single flake that configures stock
NixOS, which means you get reproducibility and atomic rollback for free.

## Why it exists

Most people running local agents are flying blind: agents run with full system
privileges and leave no record of what they did. hearth makes agent activity
legible and contained at the operating-system level.

- **Contained.** Every agent run is sandboxed with systemd isolation primitives,
  and you can narrow a single run further: declare the exact tools and hosts it
  may touch, with declared hosts enforced at the kernel by per-run nftables
  rules. See [Per-run containment](/hearth/concepts/per-run-containment/).
- **Legible.** Every run records its token count, cost, latency, and errors to a
  local SQLite database, plus a structured per-step event stream you can scrub
  through in the [replay viewer](/hearth/operations/replay/): every tool call,
  what came back, and what the permission engine said.
- **Reproducible.** The flake lock pins every input, so two builds produce the
  same system.
- **Leashed.** A [governor](/hearth/operations/governor/) puts a hard daily
  token budget on the whole box with a circuit breaker, and pushes alerts to
  your phone (Telegram or ntfy) when a run errors, trips a wire, or hits the
  cap.

## Who it is for

People running local LLMs and agents on a homelab, a workstation, or a VM who
want least-privilege isolation and a real audit trail instead of trust by default.

:::note[Status]
hearth is a work in progress. See [Project status](/hearth/project/status/) and the
[Roadmap](/hearth/project/roadmap/) for exactly what is built today.
:::

## Next steps

- [Quickstart](/hearth/getting-started/quickstart/) to validate the flake.
- [Choose your install path](/hearth/installation/choose-your-path/).
- [Architecture](/hearth/concepts/architecture/) for the system design and module map.
