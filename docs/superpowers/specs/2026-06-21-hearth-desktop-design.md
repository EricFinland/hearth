# hearth desktop (v1) design

Date: 2026-06-21
Status: approved direction, pending spec review

## Goal

Turn the Razer Blade from a text console into a cool, usable KDE desktop with a
one-hotkey hearth "command center." The look is the minimal "icy rice" aesthetic
(dark cool-blue, transparent terminals, slim status bar, neofetch readout, moody
animated wallpaper), but the system stays mouse-driven and easy to use.

This is the first concrete piece of the larger goal (a Linux setup built for
running local LLMs and agents). It is not the whole vision; later pieces are
listed under "Out of scope."

## Decisions

- Desktop: KDE Plasma 6 on X11. X11 is the stable path for KDE on this Intel +
  NVIDIA Optimus laptop and for the theming tools. Wayland is a later move.
- Login: SDDM auto-logs into the `operator` desktop session on boot. No login
  prompt on a personal laptop. The console TUI dashboard stays reachable over
  SSH.
- Window behavior: normal floating windows (click, drag, resize). No tiling.
- Aesthetic: dark icy-blue minimal. Transparent and blurred terminals, a slim
  top panel with live system stats, a neofetch-style system readout on the
  desktop, and a moody blue animated wallpaper. The command center inherits the
  same look.
- Cool layer: hybrid command center. KDE is the normal usable desktop; a
  full-screen web "command center" (live activity + agent map + GPU/CPU/RAM
  stats) is a global hotkey away. It extends the existing `hearth-mapd` web app.
- Display GPU: the Intel iGPU drives the display; the NVIDIA RTX 2060 stays for
  LLM compute (the PRIME offload setup already in place).
- Theming is declarative via `plasma-manager` (a home-manager module for KDE),
  so the look is reproducible, not hand-clicked.

## Aesthetic spec (the "feel")

- Palette: near-black background, cool blue and cyan accents, light-gray text.
- Wallpaper: a moody dark-blue animated loop (slow drifting clouds, aurora, or
  particle flow). Fallback to a high-quality static moody-blue image if the
  animated path proves unreliable on X11.
- Terminals (Konsole): transparent background with blur, a slim font, the icy
  palette. A terminal shows a neofetch/fastfetch readout on launch.
- Status bar: a thin top panel with virtual-desktop pager, a system tray, a
  clock, and live sensors: CPU, RAM, GPU, network, battery, volume.
- Desktop readout: a conky-style always-on widget showing system info (OS,
  kernel, uptime, packages, RAM, CPU, disk) in the icy theme, echoing the
  neofetch panel in the screenshot reference.
- Overall: uncluttered. Few icons, no heavy default KDE chrome.

## Architecture and components

### 1. Desktop foundation: `nixos/modules/desktop.nix`
- New module gated behind `hearth.desktop.enable` (default false; on for the
  blade host).
- Enables the X server, KDE Plasma 6, and SDDM with auto-login to `operator`
  and the Plasma X11 session as default.
- Installs daily-use apps: a web browser (Firefox), Konsole, Dolphin (files), a
  text editor, plus conky (or fastfetch) for the desktop readout.
- When `hearth.desktop.enable` is on, the blade's console auto-login to the TUI
  is dropped (SDDM owns the seat). The TUI dashboard and `hearth-runs` remain
  available over SSH.

### 2. Theming: `plasma-manager` via home-manager
- Add `plasma-manager` as a flake input and wire it into the `operator`
  home-manager configuration.
- Declare: global dark theme + icy color scheme, the animated/moody wallpaper,
  the slim top panel with sensor widgets, Konsole transparency/blur profile, and
  the Meta+A global shortcut that toggles the command center.

### 3. Command center: extend `webui/hearth_mapd.py` and the web UI
- New page route `/command`: full screen, three zones in the icy theme:
  - animated background (canvas),
  - live activity: agents (existing `/state` + SSE), recent runs, scrolling
    recent log lines,
  - system stats: GPU (name, utilization, VRAM), CPU, RAM.
  - the tycoon agent map as the centerpiece.
- New backend endpoint `/stats`: returns GPU stats (parse `nvidia-smi
  --query-gpu=...`) and CPU/RAM (read `/proc/stat`, `/proc/meminfo`). Defensive:
  any missing source returns nulls, never errors.
- Display: a frameless kiosk browser window (Firefox or Chromium in app/kiosk
  mode) pointed at `http://localhost:8770/command`, toggled by Meta+A via a
  small show/hide script bound through plasma-manager. Tap to bring up the
  cockpit full screen; tap again to return to the desktop.

## Data flow

```
agents --> SQLite (agent_state, agent_runs)         [existing]
nvidia-smi + /proc --> hearth-mapd /stats           [new]
hearth-mapd --> serves /command page + /state (SSE) + /stats
kiosk browser --> renders wallpaper + activity + stats + map, updates live
KDE --> desktop, app launching, Meta+A toggle
```

No path reads model output to choose visuals, so the UI spends zero model
tokens (same guarantee as the rest of hearth).

## Error handling and robustness

- Remote-management safety: if KDE or X fails to start, the system still reaches
  multi-user and SSH, and the previous NixOS generation is one rollback away. A
  graphics problem cannot strand the laptop.
- The command center is non-critical. If `hearth-mapd` or the kiosk window
  crashes, the desktop is unaffected. Panels degrade to placeholders when a data
  source (Ollama, nvidia-smi, the database) is down.
- Theming is declarative, so a bad theme change is reverted by rebuilding the
  previous generation.

## Testing

- On Windows (now): the `/command` page, the `/stats` parsing (with mocked
  `nvidia-smi` output and `/proc` samples), and the SSE feed, tested headlessly,
  the same way the existing agent and dashboard code was tested.
- On the blade (after deploy): SDDM and KDE start, the mouse works, the Meta+A
  command center toggles, real GPU/CPU/RAM stats render, the wallpaper animates,
  terminals are transparent. Verified with rollback ready and SSH as a fallback.

## Scope (v1, this spec)

- A usable, themed KDE Plasma desktop on X11 with auto-login.
- The icy-minimal aesthetic: animated moody wallpaper, transparent blurred
  terminals, slim status bar with live sensors, neofetch-style desktop readout.
- The command center cockpit: animated background, live activity (agents, runs,
  logs), GPU/CPU/RAM stats, and the tycoon map, on the Meta+A hotkey.
- Reproducible in Nix and deployed to the blade.

## Out of scope (later)

- Wayland.
- A local-model chat window and a spawn-agent-from-UI button in the command
  center.
- Custom native Plasma widgets (QML plasmoids).
- Multi-monitor layout.
- Broader "distro" packaging and any public release.

## Known risks and open implementation details

- Animated wallpaper on KDE X11 is the fiddliest piece. The mechanism (a video
  wallpaper plugin, mpvpaper, or a shader) is chosen at implementation time,
  with a static moody-blue wallpaper as the guaranteed fallback.
- `plasma-manager` covers most KDE settings declaratively but not every theme
  detail; anything it cannot set is documented and applied with a small
  activation script rather than left to manual clicking.
- The kiosk-window toggle (launch, focus, hide on a hotkey) needs a small helper
  script; exact window-management approach is settled during implementation.
- First graphical boot on the Optimus GPU is the main on-hardware risk; mitigated
  by rollback and SSH fallback as above.
