---
title: Telegram check-ins
description: Wire hearth to a Telegram bot so long-running agents can update you and take steering.
---

hearth can reach you on Telegram. Long-running agents send progress notes, and a
marathon run can pause to ask what to do next. It is built on the standard
library and fails silent: if no bot is configured, nothing breaks, you just get no
messages.

## What uses it

- **Marathon `--checkin`** pauses each round and waits for your reply, so you can
  steer or stop a long run from your phone. See [Autonomy](/hearth/concepts/autonomy/#marathon).
- **The growth daemon** sends a note on each merged self-improvement and a batch
  summary. See [Autonomy](/hearth/concepts/autonomy/#growth-daemon).
- **Self-evolve** sends a note when it commits a validated branch.

## Configuring it

Provide a bot token and a chat id. hearth resolves them as named
[credentials](/hearth/reference/agent-credentials/) first, falling back to
environment variables:

| Purpose | Credential name | Env var fallback |
| --- | --- | --- |
| Bot token | `telegram_token` | `TELEGRAM_BOT_TOKEN` |
| Chat id | `telegram_chat` | `TELEGRAM_CHAT_ID` |

The recommended path is to store them as encrypted secrets so they land in the
agent credentials file. See [Secrets (sops-nix)](/hearth/reference/secrets/) and
[Agent credentials](/hearth/reference/agent-credentials/).

## Under the hood

The bridge (`agent/hearth_telegram.py`) is three small functions over the Telegram
Bot API: `send` (post a message), `get_updates` (read messages since an offset),
and `wait_for_reply` (block until you reply or a timeout, default 10 minutes).
Network failures are swallowed, so a flaky connection never crashes a run.
