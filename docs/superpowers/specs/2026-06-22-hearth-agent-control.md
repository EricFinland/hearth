# hearth agent control: Claude-Code-style permission modes and live sessions

Date: 2026-06-22
Status: approved design, ready for planning

## Problem

Launching an agent from the command center "does nothing" from the user's point
of view. Investigation on the live blade showed the backend pipeline actually
works: a launch is queued, the path-watcher spawns a sandboxed `hearth-agent@`
unit, and the agent runs and writes state to the audit DB. The real problems are
experiential, not mechanical:

1. The launch form never clears and gives no "it started" feedback, so the
   button looks dead.
2. The "agents" card is a tiny text list that mixes a new agent in with old
   finished ones, so a new launch is easy to miss.
3. The agent's actual work (its reasoning, the files it writes, the commands it
   runs) goes only to the systemd journal. The work is invisible in the UI.
4. Agents are locked to a throwaway sandbox (`/var/lib/hearth/agents/<id>`), so
   even a successful run leaves no visible change on the real machine. A user
   prompt to "set up a cool i3wm desktop" ran to completion but its writes went
   into a disposable directory and vanished.

The user wants the agents to feel and behave like Claude Code: visible live
work, selectable permission modes (plan / auto / bypass), the ability to drive an
agent interactively, and real reach over the machine. The phrasing was "a mix of
OpenClaw and Claude Code."

## Decisions (locked)

- **Agent reach:** full machine, always. Agents run unsandboxed as a real user
  with sudo available. The permission mode controls *how* the agent acts and how
  much it checks in, not how far it can reach.
- **Drive styles:** both. Interactive sessions (drive one agent closely, like a
  Claude Code conversation) and fire-and-forget background workers (dispatch a
  task, watch the transcript, approve/deny risky steps).
- **Architecture:** Option C, a managed subprocess with pipes. `hearth-mapd`
  stays the relay and spawns the agent loop as a child process, talking to it
  over stdin/stdout JSON-lines. The same runtime backs both drive styles.

## The three permission modes

Mapped onto Claude Code's model:

| Mode   | Agent behavior                                                        | User involvement                                              |
|--------|-----------------------------------------------------------------------|--------------------------------------------------------------|
| plan   | Reads, explores, thinks. Changes nothing. Produces a plan and stops.  | Approve the plan, which switches the run to auto/bypass and executes. |
| auto   | Auto-runs reads and file writes. Pauses for approve/deny before shell commands, network calls, sudo, and deletes. | Click approve or deny on each gated step. |
| bypass | Does everything with no prompts. Full machine.                        | Watch the live transcript; hit stop if needed.               |

Mode is chosen at launch and switchable mid-run.

## Architecture

### Runtime and control protocol

`agent/hearth_loop.py` remains the agent brain and gains a control channel.

- The loop reads commands from **stdin** as JSON-lines and writes **events** to
  **stdout** as JSON-lines, one event per meaningful step.
- Events out:
  - `token` streamed assistant text fragment
  - `message` a full assistant turn
  - `tool_request` the agent wants to run a tool: `{id, tool, args, risk}`
  - `tool_result` `{id, output}`
  - `plan` a proposed plan (plan mode)
  - `state` one of SPAWNING / THINKING / TOOL_CALL / WAITING_APPROVAL /
    WAITING_IO / ERRORED / DONE
  - `done`
  - `error`
- Commands in:
  - `user_message` the user typed something (interactive follow-up)
  - `decision` `{id, allow: bool}` for a pending `tool_request`
  - `set_mode` `{mode}` plan / auto / bypass
  - `stop`
- When the loop hits a gated tool it emits `tool_request`, sets state
  `WAITING_APPROVAL`, and blocks reading stdin until a matching `decision`
  arrives. A deny is fed back to the model as the tool result ("user denied
  this action") so the model can adapt, like Claude Code.

This keeps all agent logic in one file and makes the loop drivable by anything
that speaks the protocol: the server, or a test harness with a scripted stdin.

### Drive path 1: interactive session

- `hearth-mapd` spawns `hearth-loop` as a child subprocess per session and holds
  its stdin/stdout pipes in a `Session` object keyed by a session id. A reader
  thread pumps the child's stdout into an in-memory event queue per session.
- New endpoints:
  - `POST /session` create a session `{task, model, mode}`, returns `{id}`
  - `GET /session/<id>/events` SSE, relays the child's stdout events
  - `POST /session/<id>/send` write a command (user_message / decision /
    set_mode / stop) to the child's stdin
- The browser watches tokens stream in, approves/denies inline, types
  follow-ups, and flips the mode live.

### Drive path 2: background worker

- Keeps the existing queue to `hearth-spawn` to `hearth-agent@<id>` systemd path,
  upgraded so the unit runs the same `hearth-loop`.
- The unit's stdout events are appended to the audit DB: enriched
  `agent_events` rows for transcript steps, and `tool_request`s become rows in a
  new `pending_actions` table.
- The UI shows the transcript on the map and lets the user approve/deny (writing
  a `decision` that the unit reads) or stop. No interactive chat.
- Default mode for background workers is **auto**, so an unattended agent cannot
  run dangerous actions without a click. A worker may be launched in bypass
  explicitly.

### Permission engine

A small `agent/permissions.py` shared by both paths. Each tool carries a risk
class:

- **safe** (always auto): `read_file`, `list_files`
- **edit** (auto in auto/bypass, gated in plan): `write_file`
- **dangerous** (gated in plan and auto, auto in bypass): `run_command`,
  `http_request`, deletes, anything via sudo

The engine is a pure function `(mode, tool, args) -> allow | gate | deny`.

- plan: denies everything except safe, injects a "you are planning, do not act"
  system note, and forces a final `plan` event before `done`.
- auto: safe and edit allowed; dangerous gated.
- bypass: everything allowed.

One optional knob: an allowlist of commands that are always auto even in auto
mode (for example `git status`, `ls`) so the user is not clicking approve
constantly. Off by default.

### Cockpit UI

The `/command` page (`webui/static/command.html`) grows a session console.

- **Launch panel** gains a mode selector (plan / auto / bypass) and two actions:
  "open session" (interactive) and "run in background" (worker). The dead-feeling
  bugs are fixed here: the form clears on launch, the button shows a spinner, and
  an interactive launch immediately opens a session so the user sees life at once.
- **Session console** (center, overlays the static map when a session is open):
  the streaming transcript. Assistant text streams in; tool calls render as cards
  (command plus output); a gated step renders an inline Approve / Deny card
  showing the exact command. The header has a live mode dropdown and a Stop
  button.
- **Agents map** stays as the at-a-glance list of all agents (sessions and
  background) with a state icon. Clicking one opens its console or transcript.

The existing icy hacker aesthetic is preserved.

## Security and audit

Full-machine reach is a deliberate, user-chosen tradeoff. The blast radius is
contained as follows:

- Interactive and background agents run unsandboxed as a real user with sudo
  available. This drops `DynamicUser` and `ProtectSystem=strict` for these units.
  This is the cost of agents that actually change the machine.
- The server stays localhost plus bearer-token only (unchanged
  `request_allowed`).
- Every tool call, every approve/deny decision, and every mode change is written
  to the audit DB with a timestamp and the arguments.
- A global kill switch `POST /stop-all` and a per-session Stop button.
- bypass mode shows a persistent red banner in the UI so an unsupervised agent is
  always obvious. plan and auto are the safe defaults; bypass is opt-in per
  launch.

## Testing

- `agent/permissions.py` gets a pure unit-test table (mode by risk to outcome)
  wired into the existing `--self-test` pattern.
- A protocol test drives `hearth-loop` with scripted stdin (deny one command,
  approve another, switch mode) and asserts the stdout event sequence. It uses
  the injectable fake `chat_fn` that the loop already supports, so it needs no
  Ollama.
- Both run in the existing CI eval gate.

## Out of scope (for now)

- Token-by-token streaming is desirable for the interactive console and will be
  attempted via Ollama streaming, but step-level events are the contract;
  per-token is best-effort.
- Multi-user access, remote (non-localhost) operation beyond the existing token,
  and per-run credential scoping are not part of this work.
- Voice input/output remains deferred.

## Components and their boundaries

- `agent/permissions.py` decides allow/gate/deny from (mode, tool, args). Pure,
  no I/O. Used by the loop.
- `agent/hearth_loop.py` runs the agent, speaks the stdin/stdout control
  protocol, and consults the permission engine. Knows nothing about HTTP or the
  DB transport beyond emitting events.
- `webui/hearth_mapd.py` relays: spawns and supervises session subprocesses,
  exposes session endpoints, and bridges background-worker events and decisions
  through the DB.
- `webui/static/command.html` renders the console and launch panel and speaks the
  session endpoints.
- `nixos/modules/spawn.nix` and related units run agents unsandboxed with the
  upgraded runtime for background workers.
