---
title: Networking & remote access
description: How hearth exposes itself with Tailscale, a tight firewall, and a short list of open ports.
---

hearth assumes a homelab on a trusted LAN with Tailscale for remote access. The
firewall is on by default and tight.

## Tailscale

Tailscale is enabled (`services.tailscale.enable = true`). Bring the host onto
your tailnet after first boot:

```sh
sudo tailscale up
```

The `tailscale0` interface is fully trusted by the firewall. Anything reachable
only over the mesh is treated as private, which is the recommended way to reach
services that should not face the LAN.

## Open ports

On the public (non-Tailscale) interface, the firewall opens only:

| Port | Service | Notes |
| --- | --- | --- |
| 22 | SSH | Key-only. Password auth is disabled. |
| 11434 | Ollama API | Homelab convenience. See the warning below. |
| 8770 | Map UI (`hearth-mapd`) | Only if `hearth.mapui.openFirewall` is true (default). |

:::caution[Do not expose Ollama publicly]
Port 11434 should not be open on a host that faces the open internet. It is
opened for homelab convenience. For a tighter setup, remove it from the firewall
and reach Ollama over Tailscale only, relying on the trusted `tailscale0`
interface.
:::

## Tightening the map UI

The web map is open on the LAN by default so other devices can view it. To keep
it private, set:

```nix
hearth.mapui.openFirewall = false;
```

Then reach it over Tailscale only. See [Map dashboard](/hearth/operations/map-dashboard/).

## Connecting

```sh
# over the LAN or Tailscale, as the admin user
ssh operator@<host-ip>
```

SSH requires a key listed in [`hearth.adminKeys`](/hearth/reference/configuration/#hearthadminkeys).
The console password (`operator` / `hearth` initially) is only a local-console
fallback; change it on first boot with `passwd`.
