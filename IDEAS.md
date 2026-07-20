# hearth Ideas Backlog

A running list of features to build into hearth later. Not committed work, just a
well-organized menu so good ideas do not get lost.

Every idea here should deepen one of hearth's three guarantees or show one off:

1. Agents are sandboxed by default.
2. Every agent run is audited.
3. The whole system is legible and reproducible from boot.

Anything with a release slot has moved to the
[roadmap](https://ericfinland.github.io/hearth/project/roadmap/) (the staged
plan from v1.2 to v2.0). What remains here is unscheduled. Effort and impact
are rough gut calls to help sequencing.

## Sandboxing

- **Bind-mount filesystem jail.** Hide the wider filesystem behind a bind-mount
  allow list instead of read-only-but-visible. Effort: medium. Impact: medium.

## Governance

- **Multi-agent scheduling with priority queues.** Sandboxed workers pull tasks
  from a queue, each with its own cgroup CPU and RAM cap. Effort: high.
  Impact: medium.

## Map UI

- **Web dashboard mirroring the TUI.** A minimal web view of the boot dashboard,
  reachable over Tailscale. Effort: medium. Impact: medium.

## Trust and supply chain

- **Signed and attested images.** cosign or sigstore: prove the image you booted
  matches the exact flake commit. Effort: medium. Impact: medium.
- **Close the loop with the mcp-audit project.** Wire the MCP audit gate so
  approvals are produced by a real scan from the separate mcp-audit scanner. Two
  projects, one story. Effort: medium. Impact: medium.
- **Boot attestation.** TPM-measured boot plus a dashboard panel that says
  "this system matches commit abc123." Effort: high. Impact: medium.

## Other agent capabilities

- **Local tool server.** A set of safe, audited tools agents can call (file read
  within the allow list, web fetch through the egress proxy), exposed to
  external clients. Effort: medium. Impact: medium.
- **Snapshot and rollback of the agent environment.** Capture models, secrets,
  and working state as a Nix closure you can roll back to. Effort: high.
  Impact: medium.
