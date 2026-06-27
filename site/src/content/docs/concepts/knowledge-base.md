---
title: Knowledge base (RAG)
description: Ground agents in your own documents and code with local retrieval.
---

hearth ships with a local knowledge base so your agents can answer grounded in your own documents and code. Everything stays on the box. Nothing leaves for a third-party API, so it is private by default and works offline.

The knowledge base lives in the same SQLite audit database the rest of hearth uses, in a `kb_chunks` table. The implementation lives in `agent/hearth_knowledge.py`.

## How it works

Ingesting a source splits its text into chunks of about 800 characters, breaking on paragraph boundaries so chunks stay coherent. Re-ingesting the same source replaces its existing chunks, so you can refresh a document without piling up duplicates.

Retrieval is semantic by default. Each chunk is embedded with a local Ollama embedding model (`nomic-embed-text` by default, configurable via the `HEARTH_EMBED_MODEL` env var and included in the declarative model manifest). A query is embedded the same way and chunks are ranked by cosine similarity.

If no embedding model is available, retrieval falls back automatically to lexical TF-IDF ranking. This means search always works, even fully offline with no models pulled. When you do have the embed model present, you get the better semantic results for free.

## Agent tools

Agents interact with the knowledge base through four tools.

- `kb_add` ingests a document. Provide a `source` name and either inline `text` or a workspace `path`.
- `kb_search` retrieves the most relevant chunks for a `query`.
- `index_dir` indexes a whole directory or repository into the knowledge base under `name/relpath`. It skips binaries, build directories (like `.git` and `node_modules`), and oversized files, and it is capped to keep ingestion bounded. This is how you make an agent project-aware: index a codebase once, then let the agent `kb_search` it. It is backed by `agent/hearth_project.py`.
- `fetch_to_kb` fetches a web page's readable text and adds it to the knowledge base in one step.

### Permission classes

The tools respect hearth's plan, auto, and bypass modes based on what they do.

- `kb_search` is a safe read.
- `kb_add` and `index_dir` are edits.
- `fetch_to_kb` is dangerous, because it performs network access.

## Auto-recall

You usually do not have to call `kb_search` by hand. Before a background agent run, hearth automatically retrieves the most relevant knowledge-base chunks, along with past memory lessons, for the goal and injects them into the agent's context.

This means agents start grounded in what you have indexed without needing an explicit search step first. Auto-recall applies to background workers and to swarm specialists through the shared agent loop.

## Example: make an agent project-aware

Point an agent at a project, index it, then ask questions about it.

```text
Index the repo at ./services/billing into the knowledge base as "billing",
then tell me how refunds are calculated and which module handles webhooks.
```

The agent calls `index_dir` to learn the codebase, then `kb_search` (or relies on auto-recall) to pull the relevant chunks before answering.

## Example: manage it from the CLI

Two CLIs let you work with the knowledge base directly on the box.

`hearth-knowledge` handles ad hoc documents.

```bash
hearth-knowledge ingest notes notes.md
hearth-knowledge search "how do we rotate API keys"
hearth-knowledge sources
```

`hearth-project` handles directory and repo indexing.

```bash
hearth-project index billing ./services/billing
hearth-project list billing
```

## Semantic vs lexical

The knowledge base upgrades itself based on what is available. With the embed model present (`nomic-embed-text` by default), retrieval is semantic and ranks chunks by cosine similarity over embeddings. Without it, retrieval falls back to lexical TF-IDF. Either way search works, so you can run grounded agents on a fresh box and improve result quality later just by pulling the embedding model.
