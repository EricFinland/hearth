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

## The egress wall

Everything above is inbound policy. Since v1.4, hearth can also enforce
per-run outbound policy: when [`hearth.egress.enable`](/hearth/concepts/per-run-containment/#the-os-layer-since-v14)
is on and a launch declares `allowed_hosts`, the run's traffic is filtered at
the kernel with nftables. The concept is covered in
[Per-run containment](/hearth/concepts/per-run-containment/); this section is
the mechanics on the box.

```nix
hearth.egress.enable = true;
```

The option lives in `nixos/modules/egress.nix` and is off by default.

### The `table inet hearth` design

All egress rules live in one dedicated nftables table, `table inet hearth`.
Each spawned run gets its own chain, and packets are matched to a chain by the
run's systemd cgroup (`system.slice/hearth-agent@<id>.service`), so a chain
binds exactly one run's processes, shell children included. A chain accepts
loopback, DNS, and the resolved addresses of the run's declared hosts, and
drops everything else with a log record. Chains are added just before a run
starts and removed when its unit stops, so an idle box carries no run chains.

### Coexistence with the NixOS firewall

The wall does not replace `networking.firewall`, and `networking.nftables`
stays off. The stock firewall keeps handling inbound policy exactly as
described above, on its default backend; `table inet hearth` is a separate
table that hearth manages directly with `nft`, and it only filters outbound
traffic from run cgroups. The kernel evaluates both rule sets, so neither
interferes with the other, and disabling the module simply deletes the hearth
table.

### The `hearth-egress` CLI

The implementation is `agent/hearth_egress.py`, exposed as a small CLI with
three subcommands:

| Subcommand | What it does |
| --- | --- |
| `apply` | Resolve a run's declared hosts and program its chain in `table inet hearth`. Run by the spawn path before the unit starts. |
| `remove` | Delete a run's chain. Run when the unit stops. |
| `watch` | Follow the journal for the wall's drop log records and write each one to the `egress_log` audit table (tool `os`, allowed `0`). Runs as the `hearth-egress-watch` bridge. |

`apply` and `remove` are invoked for you by the spawn integration; you normally
only reach for them by hand when debugging. Failure is fail-open: if `apply`
errors, the run launches anyway and the tool layer still enforces.

### Inspecting the wall

Live rules, straight from the kernel:

```sh
sudo nft list table inet hearth
```

Blocked connections, from the audit log (OS-layer drops arrive via the
`hearth-egress-watch` bridge and sit next to tool-layer denials):

```sh
curl 'http://<host>:8770/egress?blocked=1'
```

## Connecting

```sh
# over the LAN or Tailscale, as the admin user
ssh operator@<host-ip>
```

SSH requires a key listed in [`hearth.adminKeys`](/hearth/reference/configuration/#hearthadminkeys).
The console password (`operator` / `hearth` initially) is only a local-console
fallback; change it on first boot with `passwd`.
