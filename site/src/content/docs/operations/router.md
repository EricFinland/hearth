---
title: The model router (declarative routing)
description: Rule-based selection of which local model runs a launch, declared in the flake, plus a plain-English way to query the audit log.
---

On a local box the model is the main quality lever. There is no dial for "spend
more per token to get a smarter answer"; there is only the set of models you
have pulled and the question of which one runs a given task. A small model is
fast and cheap on power for a summary or a triage; a coder model earns its VRAM
on a refactor; a larger general model is worth waking up for research. Picking
by hand every time is the kind of chore that quietly stops happening. The
router, added in v1.6, moves that choice into the flake, so the right model
runs each launch without anyone deciding in the moment.

The routing decision is made by the agent loop; the policy lives in
`/etc/hearth/router.json`, rendered from the flake.

## Why route

The whole point is that model choice is a policy, not a per-launch afterthought.
You declare, once, that easy tasks get the cheap model and code tasks get the
coder model, and every launch that opts in inherits that judgment. The cheap
model handles the volume of small work; the expensive model is reserved for the
work that needs it. On local hardware that is the closest thing to a cost lever
you have, and it is now written down where you can read it, review it, and
reproduce it from boot.

## Declaring the policy

The router takes a default model and an ordered list of rules. Each rule matches
on `any_keywords` (a substring found anywhere in the goal) or on `tools` (a tool
the launch is allowed to use), and names the model to run when it matches.

```nix
hearth.router = {
  default = "llama3.2:3b";
  rules = [
    {
      any_keywords = [ "refactor" "bug" "patch" "def " "function" ];
      model = "qwen2.5-coder:7b";
    }
    {
      any_keywords = [ "research" "compare" "summarize the" "investigate" ];
      model = "llama3.1:8b";
    }
  ];
};
```

This renders to `/etc/hearth/router.json` (or point the agent loop at a file of
your own with `HEARTH_ROUTER`). The default is the fallback for anything no rule
matches: here, a small fast model carries the everyday load, code work is pulled
to the coder model, and research-shaped prompts get the larger general model.

Rules are ordered, and the first match wins, so put the most specific rules
first. A launch that mentions "refactor" hits the code rule before the research
rule ever sees it.

## How "auto" launches resolve

A launch names a model as usual. The router only enters the picture when that
model is the string `auto`. In the cockpit, the launch model picker gains an
**auto (router)** option; over the API, pass `"model": "auto"`.

When the agent loop starts an `auto` run, it resolves the model in order:

1. The first rule whose `any_keywords` or `tools` matches the launch wins, and
   its model runs.
2. If no rule matches, the router's `default` runs.
3. If there is no policy at all, the caller's own fallback model runs.

Resolution happens once, at the start of the run, against the launch's goal and
tool set. Nothing about the routing is hidden: the model that was chosen is the
model the rest of the run uses, and the reason it was chosen is recorded.

## Seeing why a model ran

The routing decision is emitted as an event, so it lands in the run's
[flight recorder](/hearth/operations/replay/) alongside every other step. When
you scrub back through a past `auto` run, the record shows which rule matched
(or that the default was used) and the model that resulted. There is no guessing
after the fact about why a given launch ran on a given model: replay has the
answer, in the same timeline as the tool calls it drove.

`GET /router` returns the active policy: the default, the ordered rules, and
their models. The cockpit renders it as a **router** card, so you can see the
live policy at a glance without reading the rendered JSON. Because the policy is
declared in the flake, that card is also a faithful picture of what is on the
box, not a separate copy that can drift.

## Ask the audit log

The second half of v1.6 turns the same local models on the audit database
itself. Every run hearth executes leaves rows in the audit tables (see
[observability and audit](/hearth/concepts/observability/)). Those rows answer
almost any question you could have about what the box has done, if you are
willing to write the SQL. The natural-language audit query lets you skip the
SQL and just ask.

`POST /ask {question}` takes a question in plain English, for example "what did
the demo agent do yesterday?" or "which runs hit an egress block this week?". A
local model translates the question into a single read-only `SELECT`, that query
is validated and run, and the model then summarizes the returned rows back into
a plain answer. All local, zero cloud: the question, the schema, and the rows
never leave the box.

The cockpit surfaces this as an **ask the audit log** card. It shows the
model's summary, the exact SQL it generated, and the result rows it ran on.

### The safety model

Letting a model write SQL that runs against your audit database is only safe
because of what sits between the model and the connection. The validation is the
whole feature, not a nicety around it. Implemented in `agent/hearth_askdb.py`,
every generated query must clear all of these gates before it runs:

- **A single statement.** One query, not a batch. Anything with a second
  statement is rejected.
- **`SELECT` only.** The statement must be a read. No `INSERT`, `UPDATE`,
  `DELETE`, `CREATE`, `DROP`, or any other write verb.
- **Audit tables only.** The query may reference only the audit tables. It
  cannot reach for anything outside them.
- **No `ATTACH`, no `PRAGMA`, no writes.** The escape hatches that could reach
  another database file or change engine behavior are all refused.
- **An enforced `LIMIT`.** The query runs bounded, so a question can never pull
  an unbounded scan back through the model.

On top of the validation, the query runs against a **read-only connection**, so
even a statement that somehow slipped every check above still could not modify
anything. Validation and a read-only connection are belt and suspenders: either
one alone would refuse a write, and both are in the path.

### An honest limit

The answer is only ever as good as the local model's SQL. A small model can
misread a question, join the wrong tables, or filter on the wrong column, and
when it does, the summary will be confidently wrong. That is why the cockpit
always shows the generated SQL and the raw result rows next to the summary, and
why `POST /ask` returns them too: you can read the query, see exactly what it
asked the database, and judge the answer for yourself. Treat the summary as a
fast first read and the SQL as the record of what actually ran. The guardrails
guarantee the query is safe, not that it is the query you would have written.

## How it fits

The router is where the [roadmap](/hearth/project/roadmap/) turns toward
autonomy and brains: the box starts choosing its own model per task, and it can
answer questions about its own history without a human writing SQL. Both halves
lean on machinery hearth already had. Routing rides the v1.3 flight recorder for
its visibility, and the audit query rides the same audit tables that
[observability](/hearth/concepts/observability/) already fills on every run.
Nothing new to trust: the same recorded truth, now easier to steer and easier to
ask about.
