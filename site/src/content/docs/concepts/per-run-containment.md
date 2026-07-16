---
title: Per-run containment
description: Declare what a single agent run may touch (tools, hosts) and catch it reaching for planted secrets.
---

The [sandbox](/hearth/concepts/sandboxing/) and [permission modes](/hearth/concepts/permission-modes/)
set the outer boundary for every agent. Per-run containment narrows that boundary
for a single launch: you declare exactly which tools and which hosts one run may
use, and hearth plants decoys that catch a run reaching for credentials it was
never asked to touch.

Everything here is declared at launch (in the cockpit or over the API) and
travels with the run through the same channel as [agent credentials](/hearth/reference/agent-credentials/).

## Capability manifests (tool allowlists)

A launch can declare a set of tools, and the run may use ONLY those tools, in
every permission mode including bypass. An unlisted tool is a hard deny: it is
filtered out of the tool list the model sees, and it is refused even if the model
tries to call it by emitting a raw tool-call as text.

In the cockpit launch panel, fill the "allowed tools" field
(`read_file,web_fetch`). Over the API, pass `tools` on `/run` or `/session`, or
run `hearth-loop --allowed-tools read_file,web_fetch` (or set
`HEARTH_ALLOWED_TOOLS`).

A mission manager propagates its manifest to the specialists it spawns, so a
scoped mission stays scoped all the way down.

## Egress allowlists

A launch can declare a set of allowed hosts, and the web tools (`web_fetch`,
`web_search`, `http_request`, `fetch_to_kb`) may reach ONLY those hosts. An entry
matches itself and its subdomains (`github.com` allows `api.github.com`), and
loopback is always allowed so local APIs and Ollama keep working. A blocked
request returns an error the model can learn from instead of a network failure.

Every outbound attempt, allowed or blocked, is recorded to the `egress_log`
audit table and readable at `GET /egress`. In the cockpit, use the "allowed
hosts" field; over the API pass `allowed_hosts`, or set `HEARTH_ALLOWED_HOSTS`.

This is tool-layer enforcement: it stops accidents and naive prompt injection,
and it makes all egress visible. It does not yet stop a run that shells out to
`curl` directly. Kernel-level enforcement (systemd `IPAddressDeny` written per
run) is the next step on this feature.

## Honeyfile tripwires

Every agent workspace is seeded with a few convincing fake secrets files
(`.aws/credentials`, `.env.production`, `secrets/api_keys.txt`). Each embeds a
unique canary token. A well-behaved agent never reads unrequested credentials;
a prompt-injected or confused one that does trips the alarm.

Detection has two layers:

- A direct read of a decoy by path, caught before the contents ever reach the
  model.
- A canary token surfacing in any tool's output, which catches a shell `cat`,
  `grep`, or similar through `run_command`.

On a trip, the run is flagged and by default killed, a row is written to the
`tripwires` audit table, the agent enters the `TRIPPED` state (the world map
flashes red with an alarm), and a [Telegram](/hearth/reference/telegram/) DM is
sent if configured. Set `HEARTH_TRIPWIRE=flag` to record and warn without
killing, or `HEARTH_DECOYS=off` to disable planting for a run.

Beyond the workspace, `modules/tripwire.nix` plants world-readable bait under
`/var/lib/hearth/decoys` and a sudo-only decoy inside the secrets directory, so a
full-machine agent that goes looking for secrets on the box finds bait first.

### Honest limits

Tripwires catch reads that pass through a hearth tool or surface a canary in tool
output. A raw file open by a shell process that never prints the contents is not
caught by these layers; kernel-level `auditd` watch rules on the decoy paths are
the planned follow-on. Output scanning can also be defeated by an agent that
deliberately encodes the token. The point of a tripwire is to catch the common
case (an injected agent grabbing what looks like credentials) loudly and
auditably, not to be an unbypassable control.

## The security scoreboard

`GET /security` and the cockpit security panel show what containment is active on
the box right now: remote auth, the rate limit, capability-manifest support,
egress activity (logged and blocked counts), tripwire status and trip count, and
daemon health. It is the one place to answer "is this box actually contained
right now?"
