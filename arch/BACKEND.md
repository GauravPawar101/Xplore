# Backend Architecture

## Entry Points

The backend operates in three deployment modes, each with a different entry point:

### Monolith — `backend/main.py`

Single FastAPI process on port 8000. Mounts **all** routers under one app with CORS and GZip middleware. Includes a startup hook to run SQL migrations and an optional background job worker loop.

```
main.py
  ├── routers/ai.py         → /explain
  ├── routers/graph.py       → /analyze, /files, /graph, /analyses, ws:/ws/analyze/github
  ├── routers/rag.py         → /rag/query, /rag/index
  ├── routers/program.py     → /program, /generate/code, /generated
  ├── routers/jobs.py        → /jobs/analyze, /jobs/{id}/status, /jobs/{id}/result
  └── routers/narrator_ws.py → ws:/ws/narrate, ws:/ws/narrate/node
```

### Microservices — `backend/gateway.py` + individual apps

Four independent FastAPI processes:

| Service    | Entry Point         | Port | Routers Mounted              |
|------------|---------------------|------|------------------------------|
| Gateway    | `gateway.py`        | 8000 | ai, narrator_ws, meta        |
| Graph      | `graph/app.py`      | 8001 | graph, meta                  |
| RAG        | `rag/app.py`        | 8003 | rag, meta                    |
| Program    | `program/app.py`    | 8004 | program, meta                |

The gateway proxies cross-service calls using `XPLORE_GRAPH_SVC_URL`, `XPLORE_RAG_SVC_URL`, and `XPLORE_PROGRAM_SVC_URL` environment variables.

### Serverless (Vercel) — `backend/api/*.py`

Each file in `backend/api/` re-exports the corresponding FastAPI app for Vercel's Python runtime:
- `api/gateway.py` — Gateway app
- `api/graph.py` — Graph app
- `api/program.py` — Program app
- `api/rag.py` — RAG app

Each adds `sys.path.insert(0, ...)` so `shared.*` imports resolve correctly.

---

## Routers (Route Handlers)

### `routers/ai.py` — AI Explanation

**Prefix:** `/explain`

| Method | Path       | Description                                  |
|--------|------------|----------------------------------------------|
| POST   | `/explain` | Sends code to LLM, returns AI explanation    |

Accepts `ExplainRequest` (code snippet + optional language), streams response from Ollama via `shared.ai._hf_chat_stream`. Uses the HuggingFace Inference API or local Ollama depending on config.

---

### `routers/graph.py` — Graph Analysis & File Explorer

**Prefix:** none (root-level)

| Method | Path                    | Description                                          |
|--------|-------------------------|------------------------------------------------------|
| GET    | `/analyze`              | Analyze local directory path, return graph JSON      |
| POST   | `/analyze/github`       | Clone GitHub repo → analyze → return graph           |
| POST   | `/analyze/upload`       | Upload ZIP → extract → analyze → return graph        |
| GET    | `/files`                | Recursive file tree for a directory                  |
| GET    | `/analyses`             | List saved analyses from Postgres                    |
| GET    | `/graph`                | Load persisted graph by `codebase_id`                |
| GET    | `/graph/code`           | Fetch source code for a single node                  |
| WS     | `/ws/analyze/github`    | Streaming GitHub analysis over WebSocket             |

**Key helper:** `build_graph_for(path, explain, persist, user_id)` orchestrates the full pipeline:
1. Instantiates `GraphBuilder` with the directory path.
2. Calls `build_graph()` to parse files and detect dependencies.
3. Serializes to React Flow JSON via `to_json()`.
4. Optionally persists nodes/edges to Postgres.
5. Optionally batch-generates summaries via `shared.ai.generate_summary`.
6. Stores result in `shared.state.graph_cache`.

**WebSocket flow** (`/ws/analyze/github`):
1. Client sends `{"url": "https://github.com/..."}`.
2. Server streams `{"type":"update", "message":"..."}` progress messages.
3. `GitHubCrawler.stream_files()` yields file batches.
4. Files are written to a temp directory, then analyzed via `build_graph_for`.
5. Server sends `{"type":"complete", "graph":{...}}`.

---

### `routers/rag.py` — Retrieval-Augmented Generation

**Prefix:** `/rag`

| Method | Path         | Description                                         |
|--------|--------------|-----------------------------------------------------|
| POST   | `/rag/query` | Hybrid keyword + vector search over codebase chunks |
| POST   | `/rag/index` | Generate embeddings for all nodes, store in Milvus  |

**Query flow:**
1. Receives `RagQueryRequest` with `codebase_id`, `query`, optional `session_id`.
2. `CodebaseRetriever` performs parallel keyword search (Postgres `pg_trgm`) and vector ANN search (Milvus).
3. Results are deduplicated and ranked.
4. If a `session_id` is provided, uses the LangChain `build_chat_chain()` for conversational follow-up with message history.

**Index flow:**
1. Fetches all non-library graph nodes for a codebase from Postgres.
2. Generates embeddings via `shared.embedding.embed_text` with bounded concurrency.
3. Upserts vectors into Milvus via `milvus_service.insert_embeddings`.

---

### `routers/program.py` — Program Graphs & Code Generation

**Prefix:** `/program` and `/generate`

| Method | Path                      | Description                                    |
|--------|---------------------------|------------------------------------------------|
| POST   | `/program`                | Create/replace a program intent graph          |
| GET    | `/program`                | Read a program graph                           |
| GET    | `/program/list`           | List all programs for authenticated user       |
| POST   | `/program/summarize`      | LLM-summarize each program node                |
| POST   | `/generate/code`          | Generate code from program graph + RAG context |
| GET    | `/generated/{gen_id}`     | Retrieve generated code from MongoDB           |
| GET    | `/generated`              | List generated code entries for user           |

**Code generation flow:**
1. Receives `GenerateCodeRequest` with program nodes, edges, optional `codebase_id`.
2. If `codebase_id` present, pulls RAG context from Milvus/Postgres.
3. Builds a prompt with program intents + RAG context.
4. Calls LLM via `shared.llm_providers.completion` (OpenAI/Anthropic/HF).
5. Parses output: extracts `FILE: path/to/file.ext` blocks into artifacts dict.
6. Saves artifacts to MongoDB via `mongo_service.save_generated_code`.

---

### `routers/jobs.py` — Background Job Queue

**Prefix:** `/jobs`

| Method | Path                   | Description                    |
|--------|------------------------|--------------------------------|
| POST   | `/jobs/analyze`        | Enqueue a graph analysis job   |
| GET    | `/jobs/{id}/status`    | Poll job status                |
| GET    | `/jobs/{id}/result`    | Retrieve job result            |

Jobs execute asynchronously through the Upstash Redis queue. See [AI_PIPELINE.md](AI_PIPELINE.md) for the job handler details.

---

### `routers/narrator_ws.py` — AI Narrator WebSocket

**Prefix:** `/ws`

| Protocol | Path              | Description                            |
|----------|-------------------|----------------------------------------|
| WS       | `/ws/narrate`     | Full interactive codebase tour         |
| WS       | `/ws/narrate/node`| Single-node deep-dive narration        |

See [AI_PIPELINE.md](AI_PIPELINE.md) for the narrator state machine.

---

## Services Layer (`backend/services/`)

Thin business-logic wrappers intended for the microservices deployment:

| File              | Purpose                                                        |
|-------------------|----------------------------------------------------------------|
| `ai_svc.py`       | Wraps AI explanation calls                                    |
| `graph_svc.py`    | Wraps graph analysis, may call Graph microservice via HTTP    |
| `program_svc.py`  | Wraps program CRUD and code generation                        |
| `rag_svc.py`      | Wraps RAG query and indexing                                  |

In the monolith, these are thin pass-through layers. In microservices mode, they handle inter-service HTTP calls.

---

## Shared Layer (`backend/shared/`)

Core utilities and infrastructure used by all routers and services.

### `shared/config.py` — Configuration

Loads all environment variables with defaults:
- **Database:** `DATABASE_URL` (Postgres), `MONGODB_URI`, `MILVUS_URI`
- **LLM:** `OLLAMA_BASE_URL`, `HF_TOKEN`, `HF_MODEL_ID`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`
- **Auth:** `CLERK_SECRET_KEY`, `CLERK_PUBLISHABLE_KEY`
- **Redis:** `UPSTASH_REDIS_URL`, `UPSTASH_REDIS_TOKEN`
- **Services:** `XPLORE_GRAPH_SVC_URL`, `XPLORE_RAG_SVC_URL`, `XPLORE_PROGRAM_SVC_URL`

### `shared/db.py` — PostgreSQL

Async PostgreSQL client using `asyncpg`:
- Connection pool management via `get_pool()` / `close_pool()`
- `run_migrations()` — Executes all SQL files in `migrations/` folder
- CRUD operations: `persist_analysis`, `persist_nodes_edges`, `load_graph`, `list_analyses`
- RAG queries: `rag_query_keyword` — trigram-based keyword search on node names/code
- Chat persistence: `save_chat_message`, `load_chat_history`

### `shared/ai.py` — LLM Interaction

Ollama-based AI functions:
- `_hf_chat_stream(messages)` — Streams chat completions from Ollama's `/api/chat` endpoint
- `generate_summary(node)` — Generates a one-line summary for a graph node
- `explain_code(code, language)` — Returns a plain-English explanation

### `shared/auth.py` — Authentication

Clerk JWT verification:
- `verify_clerk_token(request)` — Extracts and verifies JWT from Authorization header
- Uses `clerk-backend-api` SDK or falls back to manual JWKS verification
- Returns user ID on success, raises 401 on failure

### `shared/parser.py` — Code Parsing

Multi-language tree-sitter parser:
- `UniversalParser` class with lazy-cached parser/query objects
- Supported: Python, JavaScript, TypeScript/TSX, Java, Rust, C, C++, Go
- `parse_file(filepath)` → list of `ParseResult` dicts (`name`, `type`, `start_line`, `end_line`, `code`)
- Two-pass capture processing: collect definition spans, then map names to enclosing definitions

### `shared/crawler.py` — GitHub Crawler

Streams file contents from GitHub API without local clone:
- `GitHubCrawler` class with `stream_files()` async generator
- Rate-limit aware with exponential backoff
- Filters by supported extensions, skips files > 512KB
- Concurrent fetches bounded by semaphore (`MAX_CONCURRENCY=20`)

### `shared/ingest.py` — Code Ingestion

Handles codebase input:
- `clone_github_repo(url)` — Shallow git clone to temp directory
- `process_upload(file)` — ZIP extraction with security guards (zip-slip, size limits)
- `_safe_extract` — File count limit (10,000), size limit (500MB)

### `shared/embedding.py` — Embedding Generation

Ollama embedding via `/api/embeddings`:
- `embed_text(text)` → float vector (dimension 768 for `nomic-embed-text`)
- Input truncation at 8,000 chars
- Dimension padding/truncation via `_ensure_dim`

### `shared/milvus_service.py` — Vector Database

Milvus client for vector storage and ANN search:
- Collection schema: `id`, `codebase_id`, `symbol_id`, `embedding`
- `IVF_FLAT` index with inner product metric
- `insert_embeddings` — Bulk upsert (delete-then-insert by codebase)
- `search` — ANN search filtered by `codebase_id`

### `shared/mongo_service.py` — MongoDB

Document storage for generated code:
- `save_generated_code` — Stores artifacts (up to 5MB)
- `get_generated_code` / `list_generated_for_user` — Retrieval

### `shared/llm_providers.py` — Multi-Provider LLM

Unified LLM completion interface:
- `completion(provider, messages, model, api_keys)` — Dispatches to OpenAI, Anthropic, or HuggingFace
- BYOK (Bring Your Own Key) support via `api_keys` parameter
- Default models: `gpt-4o-mini` (OpenAI), `claude-3-5-haiku-20241022` (Anthropic)

### `shared/narrator.py` — Linear Narrator (Legacy)

Original BFS-based codebase tour:
- `run_narration(ws, graph_cache)` — Walks up to 20 nodes via BFS
- `run_node_narration(ws, graph_cache, node_id)` — Single-node deep dive
- Entry detection via in/out-degree heuristics

### `shared/narrator_graph.py` — Interactive Narrator (LangGraph)

State-machine narrator replacing the linear version:
- Built on `LangGraph StateGraph` with `MemorySaver` checkpointer
- States: `plan_tour` → `explain_node` → `pause` → route user input
- User actions: continue, ask question, change focus
- See [AI_PIPELINE.md](AI_PIPELINE.md) for full state machine diagram

### `shared/rag_chain.py` — RAG Chain

LangChain-based retrieval and conversational chat:
- `CodebaseRetriever` — Hybrid retriever (Postgres keyword + Milvus vector)
- `build_chat_chain()` — Conversational chat with LRU session history
- Session store bounded by `XPLORE_MAX_CHAT_SESSIONS` (default 100)

### `shared/jobqueue.py` — Job Queue

Upstash Redis-backed queue:
- `enqueue(job_type, payload)` — Creates job with UUID, stores in Redis
- `pop_job` / `set_running` / `set_result` / `set_failed` — Lifecycle management
- Keys have 3-day safety TTL

### `shared/schemas.py` — Pydantic Models

Request/response schemas for the entire API:
- `ExplainRequest`, `GithubRequest`, `ApiKeysBody`
- `RagQueryRequest`, `RagChunk`, `RagQueryResponse`
- `ProgramNodeInput`, `ProgramEdgeInput`, `ProgramGraphRequest`
- `JobAnalyzeRequest`, `GenerateCodeRequest`

### `shared/state.py` — Shared State

In-memory singletons:
- `graph_cache: dict` — Last analysis result (survives uvicorn reload)
- `get_parser()` — Lazy `UniversalParser` singleton

---

## Graph Builder — The Core Engine (`graph/builder.py`)

The `GraphBuilder` class is the heart of the analysis system. It transforms a directory of source files into a structured dependency graph.

### Pipeline

```
Directory
    │
    ▼
_collect_files()          BFS file collection, skip ignored dirs
    │                     Max files configurable
    ▼
_process_file()           tree-sitter parse per file
    │                     Extract functions, classes
    │                     Compute entry_score per node
    ▼
_create_edges()           Two-phase heuristic:
    │                     Phase 1: tokenize code, match symbol references
    │                              → CALLS / INSTANTIATES edges
    │                     Phase 2: extract external imports
    │                              → library blob nodes + USES edges
    ▼
_apply_topology_scores()  +20 bonus to zero-in-edge, positive-out-edge nodes
    │
    ▼
to_json()                 Serialize to React Flow format:
                          - Entry nodes at top
                          - Regular nodes in grid
                          - Library nodes in separate row
                          - Exclude USES edges from visual output
```

### Entrypoint Detection (4 Layers)

| Layer            | Points | Examples                                                  |
|------------------|--------|-----------------------------------------------------------|
| Convention       | 100    | `if __name__ == '__main__'`, `app.listen()`              |
| Filename         | 50     | `main.py`, `index.js`, `app.ts`, `server.go`            |
| Name match       | 10     | Functions named `main`, `run`, `start`, `init`           |
| Topology bonus   | 20     | Nodes with 0 in-edges but >0 out-edges                   |

### Edge Types

| Type          | Meaning                                  |
|---------------|------------------------------------------|
| CALLS         | Function A calls function B              |
| INSTANTIATES  | Code creates an instance of class B      |
| USES          | Code imports an external library package |

### Language Support

Tokenizers exist for: Python, JavaScript/TypeScript, Java, C/C++, Go, and a generic fallback. Each tokenizer splits source code into relevant identifier tokens for cross-reference matching.

---

## Jobs System (`jobs/handlers.py`)

Background job workers process queued tasks:

### `graph_analyze` Job
1. Clones repo or resolves local path via `shared.ingest`.
2. Calls `build_graph_for()` to parse and build the graph.
3. Persists nodes and edges to Postgres via `asyncpg` COPY.
4. Auto-enqueues a follow-up `graph_explain` job.

### `graph_explain` Job
1. Loads graph nodes from the analysis result.
2. Batch-generates summaries for user-code nodes (skips libraries).
3. Uses `shared.ai.generate_summary` for each node.
4. Flushes all summaries in one DB round-trip.
5. Aborts early if Ollama is unresponsive (all nodes in a batch fail).
