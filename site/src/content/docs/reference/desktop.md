---
title: Desktop
description: The optional KDE Plasma desktop for hearth hosts that have a screen.
---

A hearth host that has a screen (a laptop like the `blade`, not a headless VM)
can run a graphical desktop. It is optional and off by default; a server VM does
not need it.

## Enabling it

```nix
hearth.desktop.enable = true;
```

| Option | Type | Default | Meaning |
| --- | --- | --- | --- |
| `hearth.desktop.enable` | boolean | `false` | Turn on the KDE Plasma desktop. |
| `hearth.desktop.autoLoginUser` | string | `"operator"` | User auto-logged into the Plasma session at boot. |

## What you get

- **KDE Plasma 6** on X11, with **SDDM** as the display manager and **auto-login**
  into the Plasma session.
- A curated app set: Firefox, Google Chrome, Discord, Konsole, Dolphin, Kate,
  plus `conky` and `fastfetch`.
- A **conky desktop widget** showing live CPU, RAM, GPU, and network graphs and
  top processes, sitting on the desktop layer behind your windows.
- The per-user theme, wallpaper, panel, and shortcuts are declared with
  plasma-manager in `nixos/home/operator.nix`, so the look is reproducible, not
  hand-configured.
- `Meta+A` toggles the [command center](/hearth/operations/command-center/).

## Interaction with the boot dashboard

When the desktop is on, SDDM owns the screen, so hearth drops the console
auto-login that would otherwise launch the text dashboard on the TTY. The
[boot dashboard](/hearth/operations/commands/#hearth-dashboard) is still available
over SSH.

## Always-on hosts

A desktop host that doubles as an always-on agent box should not suspend. The
`blade` host, for example, disables sleep, suspend, hibernate, and lid-switch
actions so it stays reachable. Mirror that on any host you want always up.

:::note
Google Chrome and Discord are unfree packages, so the desktop module sets
`nixpkgs.config.allowUnfree = true`. The desktop is enabled on the `blade` host;
see [Hosts & images](/hearth/reference/hosts-and-images/).
:::
