---
title: Configuration reference
description: Every hearth.* NixOS option, with type, default, and what it does.
---

hearth adds a small set of options under the `hearth.*` namespace. Set them in a
host file under `nixos/hosts/` (for example `nixos/hosts/workstation.nix`). Stock
NixOS options work as normal alongside them.

## hearth.adminKeys

- **Type:** list of string
- **Default:** `[ ]`
- **Module:** `nixos/modules/admin.nix`

SSH public keys allowed to log in as the `operator` admin account. SSH password
authentication is disabled, so without at least one key here you can only reach
the box through the local console.

```nix
hearth.adminKeys = [ "ssh-ed25519 AAAAC3Nz... you@laptop" ];
```

:::caution
Set this before you build an image. A built image with no admin key is only
reachable from the physical or hypervisor console.
:::

## hearth.gpu.enable

- **Type:** boolean
- **Default:** `false`
- **Module:** `nixos/modules/gpu-nvidia.nix`

Enables the NVIDIA proprietary driver and CUDA for the discrete GPU. Off by
default on purpose: on a laptop with switchable graphics the driver is the most
likely thing to break a boot, so the first install runs without it. Turn it on
with a later `nixos-rebuild switch`, which keeps the previous generation
bootable for rollback. See [GPU passthrough](/hearth/reference/gpu-passthrough/).

```nix
hearth.gpu.enable = true;
```

## hearth.llm.enable

- **Type:** boolean
- **Default:** `true`
- **Module:** `nixos/modules/llm.nix`

Enables the LLM stack: Ollama built with CUDA, plus the model-pull service.
Setting it `false` (as the `workstation-minimal` config does) skips compiling
CUDA so a first image builds in minutes. See [Hosts & images](/hearth/reference/hosts-and-images/).

## hearth.llm.models

- **Type:** list of string
- **Default:** `[ "llama3.2:3b" "mistral:7b" ]`
- **Module:** `nixos/modules/llm.nix`

Models to pull on activation. Each string is passed verbatim to `ollama pull`.
The `hearth-model-pull` service iterates this list once Ollama is up. Pulling is
idempotent, so models already present are skipped.

```nix
hearth.llm.models = [ "llama3.2:3b" "qwen2.5:7b" ];
```

## hearth.agents.enable

- **Type:** boolean
- **Default:** `true`
- **Module:** `nixos/modules/agents.nix`

Installs the agent runtime: the `/var/lib/hearth` directory layout, the Python
and Node runtimes, and the `hearth-agent` runner on `PATH`.

## hearth.sandbox.enable

- **Type:** boolean
- **Default:** `true`
- **Module:** `nixos/modules/sandbox.nix`

Provides the reusable least-privilege systemd profile that agent services merge
in. The profile itself (`hearth.sandbox.profile`) is internal and read-only:
other modules consume it, you do not set it by hand. See
[Sandboxing & threat model](/hearth/concepts/sandboxing/).

## hearth.mcp.servers

- **Type:** list of submodule
- **Default:** `[ ]`
- **Module:** `nixos/modules/mcp.nix`

Declared MCP servers and whether each requires an audit approval before it may
start. Each entry has:

| Field | Type | Default | Meaning |
| --- | --- | --- | --- |
| `name` | string | (required) | Identifier, also the approval file name. |
| `command` | string | (required) | Command that launches the server. |
| `auditRequired` | boolean | `true` | If true, the server cannot start without an approval file. |

```nix
hearth.mcp.servers = [
  {
    name = "filesystem";
    command = "mcp-server-filesystem /var/lib/hearth/agents";
    auditRequired = true;
  }
];
```

An `auditRequired` server stays blocked until
`/var/lib/hearth/mcp-audit/<name>.approved` exists. See
[MCP audit gate](/hearth/concepts/mcp-audit-gate/).

## hearth.mapui.enable

- **Type:** boolean
- **Default:** `true`
- **Module:** `nixos/modules/mapui.nix`

Enables `hearth-mapd`, the web map that visualizes agent runtime state in the
browser. It reads the audit database and never contacts an LLM, so it costs zero
tokens. See [Map dashboard](/hearth/operations/map-dashboard/).

## hearth.mapui.port

- **Type:** port
- **Default:** `8770`
- **Module:** `nixos/modules/mapui.nix`

The TCP port `hearth-mapd` listens on.

## hearth.mapui.openFirewall

- **Type:** boolean
- **Default:** `true`
- **Module:** `nixos/modules/mapui.nix`

Opens the map port on the firewall so other devices on your network can view it.
For a tighter setup, set it `false` and reach the map over Tailscale only (the
`tailscale0` interface is already trusted). See
[Networking & remote access](/hearth/reference/networking/).
