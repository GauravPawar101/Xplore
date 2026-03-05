# Neo4j + RAG + Program-as-Graph + Code Generation

This document describes the chosen direction: **Neo4j** as the graph and RAG store, **user-defined program as nodes** (with content), a **summarization step** so intent is concise, and **code/project generation** in a target language using the graph and RAG. Users **provide their own API keys** for the model they want to use.

## 1. Overview

| Piece | Role |
|-------|------|
| **Neo4j** | Single graph DB: codebase symbols (from analysis), relationships (CALLS, IN_FILE), and optional **program nodes** (user-defined intent). Stores summaries and supports RAG queries. |
| **RAG** | Retrieve relevant symbols/summaries/code from Neo4j (Cypher + full-text or keyword) to build context for the LLM. |
| **Program as graph** | User defines the program by creating **nodes** whose **content** is free-form (“what this part should do”). Standard LLMs are not concise, so we run a **summarization** step that turns each node’s content into a short, precise intent. The **summarized graph** is the source of truth for code generation. |
| **Code generation** | From the summarized program graph (and optional existing codebase in Neo4j), generate code or a full project in a **target language** using RAG context and the user’s chosen model. |
| **User API keys** | User supplies API keys for the provider they want (OpenAI, Anthropic, Ollama, etc.). Keys can be provided via environment variables (server-side) or per request (e.g. header or body) for “bring your own key”. |

## 2. Neo4j schema

- **Codebase graph** (from tree-sitter analysis):
  - Node label: `Symbol`. Properties: `id`, `codebase_id`, `filepath`, `name`, `type`, `start_line`, `end_line`, `code`, `summary` (optional), `language`.
  - Relationship: `CALLS` from caller symbol to callee.
  - Relationship: `IN_FILE` from `Symbol` to `File` (optional). `File`: `path`, `codebase_id`.
- **Program graph** (user-defined intent):
  - Node label: `ProgramNode`. Properties: `id`, `codebase_id` (or `program_id`), `content` (user’s raw description), `summary` (concise intent after summarization), `order` (optional), `label` (optional short name).
  - Relationship: `DEPENDS_ON` or `NEXT` between program nodes to express flow or dependency.
- One Neo4j database can hold multiple codebases (by `codebase_id`) and multiple program graphs (by `program_id`).

## 3. User API keys

- **Supported providers**: Ollama (no key), OpenAI, Anthropic. Others can be added.
- **Ways to supply keys**:
  - **Environment**: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`. Server uses these if set; no key in request.
  - **Per request**: Request body or header (e.g. `X-OpenAI-API-Key`) so the user can “bring your own key” without storing on the server. Prefer header or a dedicated `api_keys: { openai?: string, anthropic?: string }` in the JSON body for summarization and code-gen endpoints.
- **Model selection**: User specifies `provider` (e.g. `openai`, `anthropic`, `ollama`) and optionally `model` (e.g. `gpt-4o`, `claude-3-5-sonnet`). Backend uses the corresponding key (from env or request) to call the API.

## 4. Flow: program nodes → summarization → code generation

1. **Define program in nodes**  
   User creates a graph of **program nodes**. Each node has **content** (free text), e.g. “When the user logs in, validate the JWT and load their profile from the database; if invalid return 401.”

2. **Summarize**  
   For each node, the backend calls the LLM (with the user’s API key and chosen model) to produce a **concise summary** of the content (2–4 sentences or bullet points). Standard models are verbose; this step makes the intent precise and stable. Summaries are stored on the `ProgramNode` in Neo4j.

3. **Generate code**  
   User asks for “generate Python FastAPI project” (or “generate TypeScript Express module”). Backend:
   - Loads the **summarized program graph** from Neo4j.
   - Optionally runs **RAG** over the **codebase graph** (if they have an existing codebase in Neo4j) for style and existing symbols.
   - Builds a prompt from: (a) summarized intents per node, (b) RAG chunks, (c) target language and stack.
   - Calls the LLM (user’s key) to generate code or project layout; returns artifacts (e.g. files, diffs).

## 5. Endpoints (to implement)

| Endpoint | Purpose |
|----------|---------|
| `GET/POST /graph?codebase_id=` | Read/write codebase graph from/to Neo4j (after analysis). |
| `POST /rag/query` | Body: `codebase_id`, `query`, `k`, optional `program_id`. Returns top-k chunks from Neo4j for RAG. |
| `POST /program` | Create or update a program graph: list of nodes `{ id, content, ... }` and edges. Stored in Neo4j as `ProgramNode`. |
| `GET /program?program_id=` | Return program graph (nodes + edges) with content and summaries. |
| `POST /program/summarize` | Body: `program_id`, `provider`, `model`, optional `api_keys`. For each node with `content`, call LLM → store `summary` in Neo4j. |
| `POST /generate/code` | Body: `program_id`, `codebase_id?`, `target_language`, `provider`, `model`, optional `api_keys`. RAG + summarized graph → LLM → return generated code/project. |

## 6. Implementation order

1. **Config and schemas**: Neo4j connection (URI, user, password from env). API key config (env + optional per-request). Pydantic models for RAG request/response, program graph, summarize, code-gen.
2. **Neo4j service**: Connect; write codebase graph (Symbols + CALLS); read graph by `codebase_id`; write/read program graph (ProgramNodes + relationships).
3. **RAG**: Cypher (and optional full-text) search over Symbols (and ProgramNodes); `POST /rag/query` returning chunks.
4. **LLM abstraction**: Factory that returns OpenAI, Anthropic, or Ollama client from provider + key; use for summarization and code-gen.
5. **Program nodes**: `POST /program`, `GET /program`; `POST /program/summarize` (batch summarization with user’s model/key).
6. **Code generation**: `POST /generate/code` — load program graph, optional RAG, build prompt, call LLM, return artifacts.

## 7. See also

- [Graph DB + RAG feasibility](GRAPH_DB_AND_RAG_FEASIBILITY.md) — broader options and phased plan.
- [Architecture](ARCHITECTURE.md) — backend/frontend layout.
