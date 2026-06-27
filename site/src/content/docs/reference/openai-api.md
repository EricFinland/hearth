---
title: OpenAI-compatible API
description: Use your local hearth models from any OpenAI client, with every call audited.
---

hearth's map and cockpit server (`hearth-mapd`, listening on port `8770`) exposes an OpenAI-compatible API. That means any OpenAI client you already use (Cursor, Continue, the `openai` SDK, LangChain, and friends) can talk to your local Ollama models without changing a line of their own code. Every call flows through hearth, so every request is recorded to the audit log.

## Endpoints

Two endpoints cover the common cases.

- `POST /v1/chat/completions` for chat completions. The alias `POST /chat/completions` also works for clients that omit the version prefix.
- `GET /v1/models` to list the local models in OpenAI list format.

## Request shape

Requests use the standard OpenAI chat schema.

```json
{
  "model": "llama3.2:3b",
  "messages": [
    { "role": "user", "content": "Explain what hearth does in one sentence." }
  ],
  "stream": false
}
```

If the `model` name you send is not one of your local models, hearth maps it to the first available local model. This means generic configs that hardcode something like `gpt-4o` still work out of the box, so you can point an existing tool at hearth without rewriting its settings.

## Streaming

Set `"stream": true` to receive Server-Sent Events. hearth forwards real token-by-token chunks straight from Ollama, so clients get the native typing effect as the model generates.

Leave `stream` off (or set it to `false`) and you get a normal OpenAI completion object back, including a `usage` block with `prompt_tokens`, `completion_tokens`, and `total_tokens`.

## Listing models

```bash
curl http://localhost:8770/v1/models
```

`GET /v1/models` returns your local models formatted as an OpenAI model list, so model pickers in OpenAI clients populate automatically.

## Authentication

On the box itself, localhost is open. No key is needed for calls from `127.0.0.1`.

Remote callers must authenticate. Send an `Authorization: Bearer <token>` header where the token is the `HEARTH_API_TOKEN` defined in `/var/lib/hearth/secrets/mapd.env`.

Some clients insist on an API key field even for local use. For localhost, any non-empty string satisfies them, since the key is ignored.

## Rate limiting

Remote callers are rate-limited with a per-IP sliding window. The default is 120 requests per minute, configurable through the `HEARTH_RATE_LIMIT` environment variable. Going over the limit returns HTTP `429`. Localhost is never rate-limited.

## Auditing

Every call is recorded to hearth's SQLite audit log under the agent name `openai-api`, capturing tokens, latency, model, and any errors. Those records surface everywhere hearth reports activity.

- `hearth-runs` on the command line
- the `/stats/history` view
- the `/metrics` endpoint

Nothing slips through unlogged, whether it came from a local script or a remote editor.

## Example: curl with streaming

```bash
curl http://localhost:8770/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "llama3.2:3b",
    "messages": [
      { "role": "user", "content": "Write a haiku about a warm hearth." }
    ],
    "stream": true
  }'
```

You will see SSE chunks stream in as the model writes, ending with a `[DONE]` marker.

## Example: Python openai SDK

Point `base_url` at your hearth host's `/v1` path and set `api_key` to the bearer token.

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://your-hearth:8770/v1",
    api_key="your-hearth-api-token",  # value of HEARTH_API_TOKEN
)

response = client.chat.completions.create(
    model="llama3.2:3b",
    messages=[
        {"role": "user", "content": "Summarize what hearth audits."}
    ],
)

print(response.choices[0].message.content)
```

For a local script running on the hearth box, you can use `http://localhost:8770/v1` and pass any non-empty string as `api_key`.

## Wiring up Cursor or Continue

Both editors accept a custom OpenAI-compatible endpoint.

1. Set the base URL (or "OpenAI base URL") to your hearth host followed by `/v1`, for example `http://your-hearth:8770/v1`.
2. Set the API key to your `HEARTH_API_TOKEN` for remote access, or any non-empty string when you are on the box.
3. Set the model to one of your local models, such as `llama3.2:3b`.

From there, the editor's chat and inline features run against your local models, and every request lands in hearth's audit log.

## Fully local, fully audited

There is no cloud hop here. Requests stay on your machine or your network, the inference runs on your own Ollama models, and every call is written to the audit log so you always have a complete record of what was asked and answered.
