---
title: Agent credentials
description: How agents use API keys by name without ever seeing the secret value.
---

Agents sometimes need an API key to call an external service. hearth lets them
use a key **by name** without the secret ever entering the model's context or
the request as written. The model says "use the credential named `openai`," and
hearth substitutes the real value at the moment the request is made.

## How it works

When the `http_request` tool builds its headers, any header value of the form
`cred:<NAME>` is resolved to the value of `<NAME>` from the agent credentials
file. A header written as:

```json
{ "Authorization": "cred:OPENAI_KEY" }
```

is sent with the real key in place of `cred:OPENAI_KEY`. The model only ever
handles the name, never the secret.

## The credentials file

Resolved names come from:

```
/var/lib/hearth/secrets/agent-credentials
```

It is a simple `NAME=VALUE` per line file. If it is missing, names resolve to
empty and the run still proceeds.

```
OPENAI_KEY=sk-...
GITHUB_TOKEN=ghp_...
```

Populate it from your encrypted secrets, not by hand in plaintext. Point a
sops-nix secret's decrypted output at this path. See
[Secrets (sops-nix)](/hearth/reference/secrets/).

## Why the agent cannot read it directly

The file lives in the `0700` secrets directory, which the sandbox already denies
to agents. For an on-demand run, the value is handed to the unit through systemd's
credential channel (`LoadCredential`), readable only at
`$CREDENTIALS_DIRECTORY/creds` and not world-readable. So the credential is
available to the tool that needs it at request time, but it is never sitting in a
place the model can list, read, or print.

This keeps the [sandbox](/hearth/concepts/sandboxing/) guarantee intact: a
prompt-injected agent that tries to exfiltrate "the OpenAI key" has nothing to
read. It can only ask for a header to be filled in by name, and that substitution
happens outside the model.

## Per-run scoping

A run can be limited to a subset of the credentials, so an agent that only needs
GitHub cannot reach your Stripe key even by name. When you launch from the
cockpit, the optional credential filter sets `HEARTH_ALLOWED_CREDS` for that run
(a comma-separated allow-list).

- If `HEARTH_ALLOWED_CREDS` is set, only names in the list resolve; every other
  `cred:` name resolves to empty.
- If it is unset, all names resolve.

So the launch decides the reach: pick `github_token` for a run and that is the
only credential it can use, no matter what the model asks for. This pairs with the
[permission modes](/hearth/concepts/permission-modes/) to scope both *what* a run
can do and *which* secrets it can touch.
