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

`hearth-mapd` exposes the whole cockpit over a plain HTTP surface:

| Method | Route | Purpose |
| --- | --- | --- |
| GET | `/world` | The [world view](/hearth/operations/world-view/) cockpit (the default). |
| GET | `/command` | The [command center](/hearth/operations/command-center/) page. |
| GET | `/` | The original map page. |
| GET | `/healthz` | Liveness check. |
| GET | `/state` | Snapshot of every agent's current state. |
| GET | `/events` | Live SSE stream of state changes. |
| GET | `/stats` | Live host stats: GPU and memory. |
| GET | `/models` | The local Ollama models available. |
| GET | `/runs` | Recent runs from the audit database. |
| GET | `/tree` | Agent lineage (manager and specialists) for the mission tree. |
| GET | `/pending` | Tool calls awaiting approval. |
| GET | `/transcript?agent=<id>` | The step-by-step transcript for one worker. |
| GET | `/growth` | The self-improvement ledger (status, lessons, branches). |
| GET | `/promote/diff`, `/promote/status`, `/promote/history` | What promotion would change, its status, and recent runs. |
| POST | `/chat` | Send a message to a model (Ollama chat); recorded as a run. |
| POST | `/run` | Enqueue an agent run (with mode, creds, and swarm/marathon/evolve/grow flags). |
| POST | `/session`, `/session/<sid>/send`, GET `/session/<sid>/events` | Open and drive an interactive session. |
| POST | `/decide` | Approve or deny a pending tool call (`{ id, allow }`). |
| POST | `/stop-all` | Kill switch: stop every session and worker, deny all pending. |
| POST | `/grow-daemon` | Start, stop, or restart the growth daemon. |
| POST | `/promote` | Build, switch, or roll back the live system. |

These power the [permission approvals](/hearth/concepts/permission-modes/), the
[autonomy modes](/hearth/concepts/autonomy/), and the
[command center](/hearth/operations/command-center/) and
[world view](/hearth/operations/world-view/).

The `/stats` endpoint returns `{ "gpu": ..., "mem": ... }`. GPU data comes from
`nvidia-smi` when it is present (name, utilization, memory used and total) and is
`null` on a host without an NVIDIA GPU. Memory comes from `/proc/meminfo`. This
feeds the live system readout on the map.

The `POST /chat` and `POST /run` endpoints are what the
[command center](/hearth/operations/command-center/) uses to chat and to launch
sandboxed agents. A launch goes through the
[on-demand spawn](/hearth/concepts/agent-engine/#on-demand-spawn) queue, so the
browser never runs anything unsandboxed.

## Access and token auth

`hearth-mapd` is open from localhost. A request from any other address must carry
a bearer token:

```
Authorization: Bearer <token>
```

The token is read from an optional secret environment file. If no token is
configured, remote requests are denied outright (localhost still works). This
keeps the chat and launch endpoints from being open on the network by default.
For remote use, configure the token and prefer reaching the server over Tailscale.
See [Networking & remote access](/hearth/reference/networking/).

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
