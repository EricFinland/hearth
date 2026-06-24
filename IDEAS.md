# hearth Ideas Backlog

A running list of features to build into hearth later. Not committed work, just a
well-organized menu so good ideas do not get lost.

Every idea here should deepen one of hearth's three guarantees or show one off:

1. Agents are sandboxed by default.
2. Every agent run is audited.
3. The whole system is legible and reproducible from boot.

Tags: `[stretch]` = already captured in the FEATURES stretch list; `[new]` = new
idea from brainstorming. Effort and impact are rough gut calls to help sequencing.

## Top picks to build first

1. **`hearth doctor`** (Security + Audit). Fast to build, proves all three
   guarantees at once, instantly demoable. Effort: low. Impact: high.
2. **Per-agent egress allowlists** (Sandboxing). The strongest "we actually
   contain agents" feature. Effort: medium. Impact: high.
3. **Tycoon map + cloud-cost-saved counter** (Map UI + Audit). The visual and the
   number that make people stop and look. Effort: medium. Impact: high.

## Sandboxing (the #1 differentiator)

- **Per-agent egress allowlists, as code.** `[new]` Declare
  `agent.allowedHosts = [ "api.github.com" "localhost:11434" ]`; hearth enforces
  it at the OS level and logs every blocked connection. Relates to the roadmap's
  per-agent network isolation goal. Effort: medium. Impact: high.
- **Honeyfile tripwires.** `[new]` Plant decoy "secret" files in the sandbox. If a
  prompt-injected agent reads one, hearth flags it, kills the run, and records it.
  A strong live demo of prompt-injection defense. Effort: medium. Impact: high.
- **Capability manifests.** `[new]` An agent declares the paths and tools it needs;
  hearth generates the minimal sandbox plus an audit record of exactly what was
  granted. Least privilege as a reviewable diff. Effort: medium. Impact: medium.
- **Syscall anomaly detection.** `[new]` Record the syscall profile of a normal
  run, alert when a run deviates. Effort: high. Impact: medium.
- **Bind-mount filesystem jail.** `[stretch]` Hide the wider filesystem behind a
  bind-mount allow list instead of read-only-but-visible. Effort: medium.
  Impact: medium.

## Audit and observability

- **Run replay / flight recorder.** `[stretch]` A scrubber timeline of each tool
  call and its output for a past run. Pairs well with the map UI. Effort: medium.
  Impact: high.
- **"Cloud cost saved" counter.** `[new]` Estimate what each run would have cost on
  a frontier cloud model and show cumulative savings. Strong portfolio hook.
  Effort: low. Impact: high.
- **Run diff.** `[new]` Same prompt, two models, side by side: tokens, cost,
  latency, output. Effort: low. Impact: medium.
- **Signed flight-recorder export.** `[new]` Export a past run as a signed artifact
  you can share as proof of what an agent did. Effort: medium. Impact: medium.

## Governance

- **Spend circuit breaker.** `[stretch]` An OS-level daily token and cost cap; when
  hit, agents pause and you get a push notification. Effort: medium. Impact: high.
- **Declarative scheduled agents.** `[new]` Cron-as-flake: run this agent at 7am
  daily with this sandbox and this budget. Effort: medium. Impact: medium.
- **Alerting via ntfy and Telegram.** `[stretch]` Push on agent completion, error,
  budget breach, or tripwire. Effort: low. Impact: medium.
- **Multi-agent scheduling with priority queues.** `[stretch]` Sandboxed workers
  pull tasks from a queue, each with its own cgroup CPU and RAM cap. Effort: high.
  Impact: medium.

## Map UI (the tycoon surface)

- **Tycoon-ify the audit data for real.** `[new]` Each agent is a building, runs
  are workers, cost is resources, the `/stats` GPU and memory feed a power-plant
  gauge. Watch an agent think as tool calls animate. The portfolio showpiece.
  Effort: medium. Impact: high.
- **Command console: run from the browser.** `[new]` The `/command` route becomes
  "launch a sandboxed agent and watch it live on the map." Effort: medium.
  Impact: medium.
- **Homelab achievements and leaderboard.** `[new]` Gamify uptime, run counts, and
  savings versus cloud. Effort: low. Impact: low (fun).
- **Web dashboard mirroring the TUI.** `[stretch]` A minimal web view of the boot
  dashboard, reachable over Tailscale. Effort: medium. Impact: medium.

## Trust and supply chain

- **Signed and attested images.** `[stretch]` cosign or sigstore: prove the image
  you booted matches the exact flake commit. Effort: medium. Impact: medium.
- **System Bill of Materials page.** `[new]` Every package, model, and module with
  its pinned hash, auto-generated from the flake. Legibility as a feature.
  Effort: low. Impact: medium.
- **`hearth doctor`.** `[new]` One command that self-tests every guarantee (sandbox
  probes, audit write, a real model inference, an egress block) and prints a
  green/red report card. Great for demos and a strong docs page. Effort: low.
  Impact: high.
- **Close the loop with the mcp-audit project.** `[new]` Wire the MCP audit gate so
  approvals are produced by a real scan from the separate mcp-audit scanner. Two
  projects, one story. Effort: medium. Impact: medium.
- **Boot attestation.** `[new]` TPM-measured boot plus a dashboard panel that says
  "this system matches commit abc123." Effort: high. Impact: medium.

## Other agent capabilities

- **Local tool server.** `[new]` A set of safe, audited tools agents can call (file
  read within the allow list, web fetch through the egress proxy). Effort: medium.
  Impact: medium.
- **Declarative model router.** `[new]` Rule-based model selection: cheap model for
  easy tasks, bigger model for hard ones. Effort: low. Impact: medium.
- **Snapshot and rollback of the agent environment.** `[stretch]` Capture models,
  secrets, and working state as a Nix closure you can roll back to. Effort: high.
  Impact: medium.
- **Natural-language audit query.** `[new]` Ask the local model questions about the
  audit DB ("what did the demo agent do yesterday?"). All local, zero cloud.
  Effort: medium. Impact: medium.
