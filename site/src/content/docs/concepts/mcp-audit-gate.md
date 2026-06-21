---
title: MCP audit gate
description: How hearth blocks MCP servers from starting until they are approved.
---

Model Context Protocol (MCP) servers extend an agent's reach into tools, files,
and networks. That makes them exactly the kind of thing you do not want starting
silently. hearth gates them.

## The rule

A declared MCP server with `auditRequired = true` cannot start until an approval
file exists for it. No approval, no start.

The gate lives in `nixos/modules/mcp.nix`. The approval file is:

```
/var/lib/hearth/mcp-audit/<name>.approved
```

If the file is missing, the server's systemd unit refuses to come up. This is a
default-deny posture: a new MCP server is blocked until someone has signed off on
it.

## Status

The gate itself is real and enforced. The scanner that produces approvals (the
step that actually audits an MCP server and writes the `.approved` file) is a
roadmap item, tracked under Day 6 packaging. See the
[Roadmap](/hearth/project/roadmap/).

:::note[Today vs. roadmap]
Built today: the systemd gate that blocks `auditRequired` servers without an
approval file. On the roadmap: the audit binary that inspects a server and grants
the approval.
:::

## Why a gate, not a blocklist

A blocklist assumes you already know which servers are dangerous. The gate
inverts that: nothing audited-required runs until it has been reviewed, so the
unknown case fails closed instead of open. It is the same principle as the
[sandbox](/hearth/concepts/sandboxing/), applied to tool surfaces rather than
processes.
