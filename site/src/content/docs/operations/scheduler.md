---
title: Standing missions (scheduler)
description: Run agent missions automatically on a schedule, the works-while-you-sleep layer.
---

hearth can run missions on their own on a schedule. This is the "works while you sleep" layer: you define a mission once, and the scheduler dispatches it for you at the times you pick. The same sandboxed spawn path the cockpit uses is what runs each mission, so there is nothing new to trust.

The scheduler lives in `agent/hearth_schedule.py`, with a NixOS module at `nixos/modules/schedule.nix`.

## How it works

A systemd timer runs `hearth-schedule --tick` on an interval (every 10 minutes by default, OnCalendar `*:0/10`). Each tick:

1. Reads the mission registry.
2. Finds the missions that are due.
3. Dispatches each due mission through the normal sandboxed spawn path.
4. Records when each mission last ran.

The timer is `Persistent`, so if the box was down when a tick was supposed to fire, the next boot catches up the missed tick instead of silently skipping it.

## Enabling it

Turn the scheduler on in your host config:

```nix
hearth.schedule.enable = true;
```

It is already on for the blade host. The tick interval is configurable:

```nix
hearth.schedule.enable = true;
hearth.schedule.interval = "*:0/10";  # OnCalendar syntax, default every 10 minutes
```

## What a mission is

Every mission carries:

- **name**, a label you choose.
- **goal**, the prompt the agent runs.
- **model**, which model to use.
- **mode**, one of `plan`, `auto`, or `bypass`.
- **kind**, one of `agent`, `swarm`, or `marathon`.
- **schedule**, when it runs.

A schedule is one of two shapes:

- `{ "every_minutes": N }` runs the mission roughly every N minutes.
- `{ "at": "HH:MM" }` runs the mission once per day at that local time.

Missions can be enabled or paused. A paused mission stays in the registry but is skipped on every tick until you resume it.

## The registry

Missions are stored as JSON at:

```
/var/lib/hearth/scheduler/schedule.json
```

The file is operator-owned, so both the scheduler and the cockpit can write to it.

## Managing missions

### From the command center

The cockpit has a "standing missions" panel. From there you can add a mission, pause or resume it, and delete it. This is the easiest way to work with the scheduler day to day.

### Over the API

- `GET /schedule` lists all missions.
- `POST /schedule` adds a mission. The body carries `name`, `goal`, `model`, `mode`, `kind`, and either `every_minutes` or `at`.
- `POST /schedule/<id>/toggle` pauses or resumes a mission.
- `POST /schedule/<id>/delete` removes a mission.

### From the CLI on the box

```bash
hearth-schedule --list   # show all missions and when each last ran
hearth-schedule --tick   # dispatch any due missions right now
```

## Declarative missions (since v1.5)

Missions you actually rely on deserve to live in the flake with the rest of
the system, not only in a JSON file the cockpit edits. As of v1.5 you can
declare missions as NixOS options, and they become part of the reproducible
system definition: rebuild the box anywhere and the same missions run.

```nix
hearth.schedule.missions = {
  morning-brief = {
    schedule = "07:00";  # once per day, local time
    prompt = "Read overnight audit activity and my notes, and write a short morning brief.";
    model = "default";
    kind = "agent";
    tools = [ "read_file" "kb_search" "write_file" ];
    allowedHosts = [ "news.ycombinator.com" ];
  };

  inbox-sweep = {
    schedule = "every:30";  # roughly every 30 minutes
    prompt = "Check for new items and triage anything urgent.";
    model = "default";
    kind = "agent";
    enabled = true;
  };
};
```

A `schedule` is either `"HH:MM"` (once per day at that local time) or
`"every:N"` (roughly every N minutes). Each mission also takes `prompt`,
`model`, `kind`, optional `tools` and `allowedHosts`, optional `creds`
(credentials granted by name), and `enabled`.

### How it renders

The option set renders to `/etc/hearth/missions.json` at rebuild. On each
tick, the scheduler merges these "nix" missions with the cockpit-created ones
from the registry and treats the merged set as one schedule. Last-run state
for nix missions is kept in a sidecar file under the scheduler's state
directory, never in `/etc/hearth/missions.json` itself, so the rendered file
stays pure config: rebuilding produces the same file every time, and the
scheduler still knows what already ran.

### Containment rides along

`tools` and `allowedHosts` are not decoration. Every launch of a nix mission
carries them as its [capability manifest and egress allowlist](/hearth/concepts/per-run-containment/),
composing the v1.1 manifests and the v1.4 OS-level wall. The
`morning-brief` mission above can use only its three listed tools and can
reach only `news.ycombinator.com` (and subdomains), enforced at the tool layer
and, where the egress module is on, at the kernel.

### In the cockpit

Nix missions show up in the standing missions panel alongside the rest, marked
with a `nix` tag. They are read-only there: the source of truth is your flake,
so `POST /schedule/<id>/toggle` and `POST /schedule/<id>/delete` return 400
for a nix mission. To pause one, set `enabled = false` and rebuild; to change
one, edit the flake. Cockpit-created missions keep working exactly as before.

## Examples

### A daily 9am marathon

Add a mission that summarizes your notes every morning at 9:00 local time, run as a marathon:

```bash
curl -X POST http://localhost:PORT/schedule \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "summarize my notes",
        "goal": "Read my notes from the last day and write a concise summary of what changed and what needs follow-up.",
        "model": "default",
        "mode": "auto",
        "kind": "marathon",
        "at": "09:00"
      }'
```

### An every-N-minutes mission

Add a mission that runs roughly every 30 minutes as a single agent:

```bash
curl -X POST http://localhost:PORT/schedule \
  -H 'Content-Type: application/json' \
  -d '{
        "name": "inbox sweep",
        "goal": "Check for new items and triage anything urgent.",
        "model": "default",
        "mode": "auto",
        "kind": "agent",
        "every_minutes": 30
      }'
```

## Safety and visibility

Scheduled missions are not a side channel. Each one runs sandboxed and audited exactly like any other run, through the same spawn path the cockpit uses. That means every mission shows up in the cockpit and in the audit log, so you can see what ran, when, and what it did while you were away.
