---
title: Tycoon map & bill of materials
description: v1.7 turns the world view into a facility you can read at a glance (buildings, workers, a power plant, fuel, trophies) and adds a page listing every pinned hash on the machine.
---

v1.7 "Tycoon" is the spectacle payoff for everything the audit log has been
recording since v0.1. The [world view](/hearth/operations/world-view/) grows a
skyline: every kind of agent you run becomes a building, every run is a worker
walking to it, the GPU is a power plant, today's token spend is fuel, and
milestones unlock trophies. Alongside it, `/sbom` lists every package, model,
and module on the box with the hash that pins it.

Like the rest of the map, none of this calls a model. It is the audit database
and the build, drawn.

## The skyline

A band along the top of the world view, above the crew rooms.

**Buildings are agent families.** A family is the run name with its random
suffix stripped: `web-1ec9641b` and `web-2fd0a1c3` are both runs of `web`,
`daily-pulse` is the declared mission of the same name. The busiest families
get a lot on the street.

- **Height** grows with the log of lifetime runs: one run is a one-storey shed,
  about 32 runs is a six-storey block with a blinking antenna. Logarithmic, so
  one chatty mission does not dwarf everything else.
- **Windows** flicker lit while a worker is on shift, keep a few lights on if
  the family ran in the last day, and go dark otherwise.
- **A red badge** counts runs that ended in an error. A building where half the
  runs errored is outlined red.
- **Hover** a building for its runs, workers on shift, tokens, dollars saved
  versus the cloud, errors, last run, and the models it has used.

**Workers walk to their building.** A run that is not part of a crew spawns at
the entrance and walks to its family's door, then queues there while it works.
When it finishes it celebrates and walks back out. Crews (a manager and its
specialists) keep their labelled rooms on the floor below.

**The growth workshop.** If the
[growth loop](/hearth/concepts/autonomy/#growth-daemon) has ever run, it has an
amber building with its lifetime tally: validated, failed, pass rate. While the
loop is off, the windows are boarded and the sign says CLOSED. See the
[growth loop retrospective](/hearth/project/growth-retrospective/) for why it is
closed on the reference box.

**The power plant.** A cooling tower and turbine hall at the end of the street.
Smoke from the stacks follows GPU utilization (and turns orange above 85%), the
gauge needle on the hall is GPU load, and the battery beside it is VRAM. It
appears whenever `nvidia-smi` is available.

On a narrow screen the street gets shorter and the quietest buildings give up
their lots first; the workshop keeps its place when there is room.

## Fuel

The amber `fuel` chip in the HUD is today's token spend. With the
[governor](/hearth/operations/governor/) on, it reads against the daily cap
(`fuel 12.4k / 500k`), turns red at 85%, and says BREAKER once the circuit
breaker opens. Without a cap it shows today's total. Dollars saved stays in its
own green chip.

## Trophies

The `trophies` button in the launch bar opens the trophy room. Each achievement
is computed from the audit log, so it unlocks the moment the facility actually
does the thing, and where the log can say, the card shows the date it happened.

| Trophy | Unlocks when |
| --- | --- |
| First light | the first run is recorded |
| Busy floor | 25 runs |
| Centurion | 100 runs |
| Megawatt | a million tokens processed locally |
| Cloud dodger | $25 of frontier-cloud spend avoided |
| Polyglot | three different local models have done real work |
| Night shift | a run started between midnight and 5am (box time) |
| Seven-day streak | runs on seven consecutive days |
| Clean sheet | 20 runs in a row without an error |
| The wall holds | the [egress wall](/hearth/concepts/per-run-containment/) bounced a connection |
| Honeypot | a [tripwire](/hearth/concepts/per-run-containment/) caught a decoy read |
| Black box | a run was captured by the [flight recorder](/hearth/operations/replay/) |
| Self-made | the growth loop validated its own improvement |

Locked trophies show a progress bar. When one unlocks while the map is open, a
gold card slides in from the right. The first poll after the page loads is a
baseline, so reloading never replays old unlocks.

## The bill of materials

`http://<host-ip>:8770/sbom` (linked from the HUD as `sbom`) is one page that
answers "what exactly is on this machine":

- **This machine:** host name, NixOS version, the git commit the system was
  built from, the kernel, and counts.
- **Hearth modules:** every `hearth.*` module and whether it is switched on.
- **Flake inputs:** each input's source, locked rev, `narHash`, and lock date,
  read from `flake.lock` at build time.
- **Models:** every Ollama model with its digest, parameter count,
  quantization, and size, read live.
- **Hearth services:** every `hearth-*` systemd unit and what it does.
- **System packages:** every system and per-user package with its version and
  the store hash that pins that exact build.

A filter box searches all of it, and clicking any hash copies it. The raw data
is at `/sbom.json`.

The build half is rendered by `nixos/modules/sbom.nix` into
`/etc/hearth/sbom.json` on every rebuild, so it can never drift from what is
installed. It is on by default; turn it off with:

```nix
hearth.sbom.enable = false;
```

The commit comes from `system.configurationRevision`, which the flake now sets
from the git revision (`nixos-version --configuration-revision` reports it too).
A build from a dirty tree reports the dirty revision; a build from a plain
directory reports none.

## Endpoints

| Endpoint | What it returns |
| --- | --- |
| `GET /tycoon` | `{buildings, achievements, unlocked, treasury, grow_active}` |
| `GET /achievements` | `{achievements, unlocked}` |
| `GET /sbom` | the bill of materials page |
| `GET /sbom.json` | the build-time manifest merged with live model digests |

`/tycoon` is cached for 20 seconds, and it reads only small tables
(`agent_runs`, `agent_state`, `egress_log`, `tripwires`, `run_steps`,
`learnings`), never the event and transcript tables that can hold millions of
rows. The logic lives in `agent/hearth_tycoon.py`, which you can also run by
hand on the box:

```sh
python3 agent/hearth_tycoon.py achievements
python3 agent/hearth_tycoon.py buildings
python3 agent/hearth_tycoon.py --self-test
```
