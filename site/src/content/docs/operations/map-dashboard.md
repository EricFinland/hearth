---
title: Map dashboard
description: The hearth-mapd web map that visualizes agent runtime state in the browser.
---

`hearth-mapd` serves a browser-based map that visualizes agent runtime state. It
reads the live state the agent runtime writes plus the audit database, and
streams updates to the page. It never contacts an LLM, so the dashboard itself
costs zero tokens.

It is separate from the terminal [boot dashboard](/hearth/operations/commands/#hearth-dashboard):
the TUI is what you see on login, the map is a visual surface you open in a
browser from any device on your network.

## Enabling and reaching it

The map is on by default. It listens on port `8770` and, with
`hearth.mapui.openFirewall` left at its default, is reachable from other devices
on your LAN:

```
http://<host-ip>:8770
```

Configure it with the [`hearth.mapui.*` options](/hearth/reference/configuration/#hearthmapuienable):
turn it off, change the port, or close the firewall and reach it over Tailscale
only. See [Networking & remote access](/hearth/reference/networking/).

## Endpoints

`hearth-mapd` exposes a small HTTP surface:

| Route | Returns | Purpose |
| --- | --- | --- |
| `/` | HTML | The map page. |
| `/healthz` | `ok` | Liveness check. |
| `/state` | JSON | A snapshot of every agent's current state. |
| `/events` | SSE | A live stream of state changes for the page. |
| `/stats` | JSON | Live host stats: GPU (via `nvidia-smi`) and memory (from `/proc/meminfo`). |
| `/command` | HTML | The command console page. |

The `/stats` endpoint returns `{ "gpu": ..., "mem": ... }`. GPU data comes from
`nvidia-smi` when it is present (name, utilization, memory used and total) and is
`null` on a host without an NVIDIA GPU. Memory comes from `/proc/meminfo`. This
feeds the live system readout on the map.

## How it runs

The service runs as the `hearth` user with light hardening (`ProtectSystem`,
`ProtectHome`, `NoNewPrivileges`, `PrivateTmp`). It only reads the audit database
at `/var/lib/hearth/runs/audit.db` and serves its static files, so its write
access is limited to the runs directory.

```sh
# check it is up
systemctl status hearth-mapd
curl -s http://localhost:8770/healthz

# see the raw state and stats the page consumes
curl -s http://localhost:8770/state | jq .
curl -s http://localhost:8770/stats | jq .
```

:::note[Active development]
The map and its command console are an evolving surface. The `/stats` readout and
the `/command` page are recent additions; expect them to keep growing.
:::
