---
title: Growth loop retrospective
description: Three months of hearth improving itself on the reference blade, what the numbers say, and why it is paused as of v1.7.
---

From June 26 to September 29, 2026, the reference blade ran the
[growth daemon](/hearth/concepts/autonomy/#growth-daemon) around the clock:
hearth proposing, implementing, and validating changes to its own config with a
local model, 95 days without anyone driving it. As of v1.7 it is paused. This
page is the honest account of how it went, pulled straight from the audit log.

## The numbers

| | |
| --- | --- |
| Days running | 95 (2026-06-26 to 2026-09-29) |
| Batches completed | 1,064 (12 cycles each, then a pause) |
| Improvements attempted | 15,074 |
| Passed the `nix flake check` gate | 2,223 (14.7%) |
| Failed the gate | 12,851 |
| Service restarts between batches | 1,215 |
| Commits compounded into the grow repo's `main` since its last reseed | 1,604 |
| Changes promoted to the live system | 0 (one build-check in June, no switch) |
| Distinct proposals, out of 15,074 attempts | 4,870 |
| Attempts at the single most repeated failing idea | 336 |
| Share of all `agent_events` rows written by the loop | 99.95% (1.76M rows) |
| Audit database size when it stopped | 2.19 GB |
| Model | `qwen2.5-coder` (7.6B, Q4_K_M), 4.7 GB of a 6 GB GPU while loaded |

Month by month, the pass rate never found a trend:

| Month | Validated | Failed | Pass rate |
| --- | ---: | ---: | ---: |
| June (from the 26th) | 29 | 139 | 17% |
| July | 363 | 2,259 | 14% |
| August | 1,186 | 4,507 | 21% |
| September | 645 | 3,741 | 15% |

The last full batch before the pause finished 12 cycles with 0 validated.

## What worked

**The safety design held, completely.** In 95 days and 15,000 attempts the loop
never touched the running system. It only ever edited its own isolated copy
(`/var/lib/hearth/grow-repo`), every change went through the flake check, and a
validated branch was merged into the grow repo's `main` only if `main` still
passed afterward. The compounded baseline was never left broken. Not one
incident traces back to the loop. That was the promise in
[self-evolve](/hearth/concepts/autonomy/#self-evolve), and it was kept.

**It was a real endurance test for everything around it.** A process that
restarts 1,215 times and calls a model about 800,000 times is a good way to find
out whether the audit log, the state tables, the map, and the promote watchdog
hold up. They did.

**It is fully auditable after the fact.** Every number on this page came from
the audit database with read-only queries. Nothing had to be reconstructed.

## What did not

**Proposals collapsed into a handful of ideas.** 15,074 attempts produced only
4,870 distinct proposals, and most of those are near-duplicates. The loop kept
coming back to "add a minor config option with a safe default" (the top
variant alone was attempted 336 times) and "add a self-test assertion". A 7B
coder model asked open-endedly for "one small safe improvement" converges on the
same few safe-sounding shapes.

**Memory did not stop repeats.** The design says recalling past lessons keeps the
loop from retrying improvements that already failed. In practice, keyword recall
surfaced the lessons and the model proposed the same idea anyway. The ledger
filled with the same failure, phrased slightly differently each time.

**Validated did not mean valuable.** What passed the gate was what the gate can
check: an option that evaluates, an assertion that holds. The flake check proves
a change is harmless, not that it is useful. The 2,223 validated changes are
overwhelmingly options nobody reads and assertions that restate a default.

**Nothing went live.** The last step is a human reviewing the compounded diff
and promoting it. With 1,604 small commits of that quality, there was never a
diff worth reviewing, so nobody did. A loop that needs human review is bounded
by human interest in its output.

**It cost more than it looked like.** The model sat in 4.7 GB of a 6 GB GPU
for most of every day (it only unloaded during the pauses between batches),
which is VRAM every other local workload on the box had to share. And 99.95% of the event log is the loop talking to itself, which is what
grew the audit database to 2.19 GB.

## What changed in v1.7

- **Paused on the reference blade.** `hearth.grow.enable = false` in
  `nixos/hosts/blade.nix`, with the numbers above in the comment. The module
  stays in the tree, still default off, still opt-in.
- **The ledger is kept.** Every lesson stays in the audit database. On the
  [tycoon map](/hearth/operations/tycoon/) the loop is now a shuttered
  growth workshop with its lifetime tally, and the "Self-made" achievement it
  earned in June stays earned.
- **The live-config default moved.** The loop, the agent self-knowledge tools,
  and the promote diff all defaulted to a hand-copied snapshot directory
  (`/home/operator/hearth-desktop`) that had drifted behind the real deploy
  clone. They now default to `/home/operator/hearth`, the git checkout the box
  is actually rebuilt from.
- **The box was cleaned up.** Three stale hand-copied trees of the repo were
  archived into a single tarball and removed, and the loop's model is no longer
  pinned in VRAM.

## If you turn it back on

The loop is worth another run when it has something better to chew on than an
open-ended "improve yourself". The changes that would make the difference:

- **A backlog, not free proposals.** Feed it concrete, human-written tasks
  (the Backlog section of the [roadmap](/hearth/project/roadmap/#backlog), a
  label on issues) instead of asking it to invent work.
- **Deduplicate before spending a cycle.** Compare a new proposal against past
  ones by embedding similarity (the [knowledge base](/hearth/concepts/knowledge-base/)
  already has local embeddings) and skip near-repeats of known failures.
- **A gate that measures usefulness.** A change should have to make a test pass
  that failed before, not just leave the flake evaluating.
- **A retention policy.** Keep the lessons, roll up or drop the per-step events
  after a week.
- **A budget.** The [governor](/hearth/operations/governor/) caps agent runs;
  the loop should be capped the same way, in hours of GPU per day.

Until then, the most useful thing the growth loop produced is this page.
