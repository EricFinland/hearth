---
title: The governor (budget & alerts)
description: A hard daily token cap enforced by a circuit breaker, and unified push alerts to Telegram and ntfy.
---

The [scheduler](/hearth/operations/scheduler/) is the works-while-you-sleep
layer, and a layer that works while you sleep needs a leash. An agent stuck in
a retry loop at 3am does not stop because you would have stopped it; it stops
when something in the system says stop. On a local GPU the bill is electricity
and thermals rather than an API invoice, but the failure mode is the same:
unbounded spend by an unattended system. The governor, added in v1.5, is that
something. It gives the whole box a hard daily token budget with a circuit
breaker, and a notification path so the box can reach your phone when
something needs you.

The breaker lives in the agent loop; alerting lives in
`agent/hearth_notify.py`.

## The spend circuit breaker

You set one number: the total tokens all runs together may consume per day.

In the flake:

```nix
hearth.governor.dailyTokenCap = 2000000;
```

Or as an environment variable:

```bash
HEARTH_DAILY_TOKEN_CAP=2000000
```

Unset means no cap, which is the pre-v1.5 behavior.

### What happens at the cap

The agent loop checks the audit database before each model call, so the
budget is enforced against real recorded usage, not an estimate kept in
memory. When the day's total reaches the cap:

- Running agents halt gracefully with the error
  `budget: daily token cap reached`. The run ends as a normal audited failure,
  not a kill; whatever the agent had done up to that point is preserved and
  visible in the audit log and [replay](/hearth/operations/replay/).
- New runs refuse to start, whatever the source: cockpit launches, API calls,
  scheduled missions, swarm children.
- A push notification fires through the alerting fan-out (below), so you find
  out when it happens, not the next morning.
- The cockpit shows "BREAKER OPEN".

The cap resets at local midnight, and the breaker closes on its own once a new
day's usage is under the cap. There is nothing to reset by hand.

### Watching the budget

`GET /budget` returns the cap, today's usage, and the breaker state. The
cockpit renders it as a budget card with a live progress bar, so you can see
at a glance how much of the day's leash is left. The
[security scoreboard](/hearth/concepts/per-run-containment/#the-security-scoreboard)
gains two chips: one for the breaker (configured, and open or closed) and one
for alerting (which channels are wired up).

## Unified alerting

Before v1.5, hearth could send a Telegram DM from a couple of places. v1.5
unifies this into one fan-out, `agent/hearth_notify.py`, that delivers every
alert to every configured channel:

- **Telegram**, using the existing bot credentials from
  [Telegram check-ins](/hearth/reference/telegram/). Nothing new to set up.
- **ntfy**, a simple push service. Set a topic and every alert lands as a push
  notification on any device subscribed to it:

```nix
hearth.governor.ntfyTopic = "my-hearth-blade";
# hearth.governor.ntfyUrl defaults to https://ntfy.sh; point it at a
# self-hosted ntfy server to keep alerts on your own infrastructure.
```

Or `HEARTH_NTFY_TOPIC` as an environment variable.

Alerts fire on error, on a [tripwire](/hearth/concepts/per-run-containment/#honeyfile-tripwires)
trip, and on a budget breach, always. Successful completion is opt-in: set
`notifyDone` in the flake (or `HEARTH_NOTIFY_DONE`) if you also want a ping
when a run finishes cleanly.

Delivery is best-effort by design. A notification failure is logged and
swallowed; it never blocks or fails a run. The alerting path observes the
system, it is not in the loop.

### What this feels like

Install the ntfy app on your phone, subscribe to your topic, and the box can
wake you up for the right reasons. If a prompt-injected mission reads a
honeyfile at 3am, the run is killed, the trip is audited, and your phone
buzzes: tripwire tripped, agent name, decoy path. You can glance at it, see
that the run is already dead and contained, and go back to sleep. In the
morning, the [replay viewer](/hearth/operations/replay/) has the whole story.

## How it composes

The governor is a global backstop, not a replacement for per-run scoping.
[Per-run containment](/hearth/concepts/per-run-containment/) bounds what a
single run may touch; the governor bounds what the whole box may spend in a
day and makes sure you hear about the events that matter. Scheduled missions
declared in the flake get both: their launches carry manifests and egress
allowlists, and every one of them counts against the same daily budget. See
the [roadmap](/hearth/project/roadmap/) for where this goes next.
