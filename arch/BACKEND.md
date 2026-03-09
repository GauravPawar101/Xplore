# Backend Architecture

## Entry Points

The backend supports three deployment modes, each with a different entry point:

### Monolith — `backend/main.py`

Single FastAPI process on port 8000. Mounts all routers under one app with CORS, GZip, and a custom preflight middleware. Includes a startup lifespan that initialises the asyncpg pool and spawns an in-process background job worker thread.

```
main.py
  ├── routers/meta.py          → /health
  ├── jobs/router.py           → /jobs/analyze, /jobs/{id}/status, /jobs/{id}/result
  ├── routers/graph.py         → /analyze, /files, /graph, /analyses, ws:/ws/analyze/github
  ├── routers/ai.py            → /explain, ws:/ws/explain, ws:/ws/chat
  ├── routers/rag.py           → /rag/query, /rag/index
  ├── routers/program.py       → /program, /generate/code, /generated
  └── routers/narrator_ws.py   → ws:/ws/narrate, ws:/ws/narrate/node
```

**Windows event-loop fix:** On Python 3.10+ Windows, `main.py` switches to `WindowsSelectorEventLoopPolicy` to avoid known incompatibilities between asyncpg and the default `ProactorEventLoop`.

**Note:** `main.py` imports `jobs/router.py`, not `routers/jobs.py`. The file `routers/jobs.py` is an identical duplicate that is not mounted by any entry point.

### Microservices — `backend/gateway.py` → `backend/gateway/app.py`

Four independent FastAPI processes behind a gateway:

| Service   | Entry Point                      | Port | Routers Mounted               |
|-----------|----------------------------------|------|-------------------------------|
| Gateway   | `gateway.py` → `gateway/app.py` | 8000 | ai, narrator_ws, meta (local); graph/rag/program (proxied) |
| Graph     | `services/graph_svc.py`          | 8001 | graph, meta                   |
| RAG       | `services/rag_svc.py`            | 8003 | rag, meta                     |
| Program   | `services/program_svc.py`        | 8004 | program, meta                 |

The gateway serves AI and narrator WebSocket endpoints locally and reverse-proxies all other routes via `httpx.AsyncClient` using a prefix-based routing table:

```python
PROXY_PREFIXES = {
    "/jobs":       GRAPH_SVC_URL,
    "/analyze":    GRAPH_SVC_URL,
    "/files":      GRAPH_SVC_URL,
    "/graph":      GRAPH_SVC_URL,
    "/analyses":   GRAPH_SVC_URL,
    "/rag":        RAG_SVC_URL,
    "/program":    PROGRAM_SVC_URL,
    "/generate":   PROGRAM_SVC_URL,
    "/generated":  PROGRAM_SVC_URL,
}
```

Microservice URLs are configured via environment variables: `EZDOCS_GRAPH_SVC_URL`, `EZDOCS_RAG_SVC_URL`, `EZDOCS_PROGRAM_SVC_URL`.

An AI microservice definition exists (`services/ai_svc.py`, port 8002) but is not deployed separately — AI endpoints are served in-process by the gateway.

Alternative standalone apps also exist at `program/app.py` and `rag/app.py`, which are functionally equivalent to the `services/` versions and are used as Docker Compose targets (`uvicorn program.app:app`, `uvicorn rag.app:app`).

### Serverless (Vercel) — `backend/api/*.py`

Each file in `backend/api/` re-exports a FastAPI app for Vercel's Python runtime:
- `api/gateway.py` — Builds its own FastAPI app with ai, narrator_ws, and meta routers (WebSockets unsupported on Vercel).
- `api/graph.py` — Imports from `graph.app`.
- `api/program.py` — Imports from `program.app`.
- `api/rag.py` — Imports from `rag.app`.

Each adds `backend/` to `sys.path` so `shared.*` imports resolve.

---

## Routers (Route Handlers)

### `routers/meta.py` — Health Check

**Prefix:** none

| Method | Path      | Description            |
|--------|-----------|------------------------|
| GET    | `/health` | Returns `{"status": "ok"}` |

---

### `routers/ai.py` — AI Explanation & Chat

**Prefix:** none

| Method    | Path          | Description                               |
|-----------|---------------|-------------------------------------------|
| POST      | `/explain`    | Sends code to LLM, returns explanation    |
| WebSocket | `/ws/explain` | Streaming code explanation over WebSocket |
| WebSocket | `/ws/chat`    | Streaming conversational chat             |

Accepts `ExplainRequest` (code + optional context, callers, callees). Uses `shared.ai` for Ollama/HF streaming.

---

### `routers/graph.py` — Graph Analysis & File Explorer

**Prefix:** none

| Method    | Path                   | Description                                     |
|-----------|------------------------|-------------------------------------------------|
| GET       | `/analyze`             | Analyze local directory, return graph JSON       |
| POST      | `/analyze/github`      | Clone GitHub repo → analyze → return graph       |
| POST      | `/analyze/upload`      | Upload ZIP → extract → analyze → return graph    |
| GET       | `/files`               | Recursive file tree for a directory              |
| GET       | `/analyses`            | List saved analyses from Postgres                |
| GET       | `/graph`               | Load persisted graph by `codebase_id`            |
| WebSocket | `/ws/analyze/github`   | Streaming GitHub analysis with progress updates  |

**Key helper:** `build_graph_for(path, max_files, codebase_id, user_id)` orchestrates the full pipeline:
1. Instantiates `GraphBuilder` with the directory path.
2. Calls `build_graph()` to parse files, detect dependencies, and run reconciliation.
3. Serializes to React Flow JSON via `to_json()`.
4. Optionally persists nodes/edges to Postgres via `shared.db`.
5. Stores result in `shared.state.graph_cache`.

**Local imports:** `shared.crawler` (GitHubCrawler), `shared.ingest`, `graph.builder` (GraphBuilder, IGNORED_DIRS), `shared.schemas`, `shared.state`, `shared.db`.

---

### `routers/rag.py` — Retrieval-Augmented Generation

**Prefix:** `/rag`

| Method | Path         | Description                                         |
|--------|--------------|-----------------------------------------------------|
| POST   | `/rag/query` | Keyword (Postgres) + optional vector (Milvus) search |
| POST   | `/rag/index` | Generate embeddings for all graph nodes, store in Milvus |

**Query flow:**
1. Receives `RagQueryRequest` with `codebase_id`, `query`, `k`, optional `program_id`, `use_vector` flag.
2. Keyword search via `shared.db.rag_query_keyword()` (ILIKE on name, filepath, code, summary).
3. If `use_vector` is true, also runs `shared.embedding.embed_text()` → `milvus_service.search()`.
4. Returns combined `RagQueryResponse` with deduplicated chunks.

**Index flow:**
1. Fetches all non-library graph nodes for a codebase from Postgres.
2. Generates embeddings via `shared.embedding.embed_text` with bounded concurrency.
3. Upserts vectors into Milvus via `milvus_service.insert_embeddings`.

---

### `routers/program.py` — Program Graphs & Code Generation

**Prefix:** none

| Method | Path                      | Description                                    |
|--------|---------------------------|------------------------------------------------|
| POST   | `/program`                | Create/replace a program intent graph          |
| GET    | `/program`                | Read a program graph by `program_id`           |
| GET    | `/program/list`           | List programs for authenticated user           |
| POST   | `/program/summarize`      | LLM-summarize each program node                |
| POST   | `/generate/code`          | Generate code from program graph + RAG context |
| GET    | `/generated/{gen_id}`     | Retrieve generated code from MongoDB           |
| GET    | `/generated`              | List generated code entries for user           |

**Local imports:** `shared.db`, `shared.mongo_service`, `shared.auth` (get_current_user_optional), `shared.llm_providers` (completion), `shared.schemas`.

---

### `routers/narrator_ws.py` — AI Narrator WebSocket

**Prefix:** none

| Protocol  | Path               | Description                     |
|-----------|--------------------|---------------------------------|
| WebSocket | `/ws/narrate`      | Full interactive codebase tour  |
| WebSocket | `/ws/narrate/node` | Single-node deep-dive narration |

Uses `shared.narrator` (linear BFS narrator). Loads graph from either Postgres (`codebase_id` provided) or in-memory `graph_cache`.

---

### `jobs/router.py` — Background Job Queue

**Prefix:** `/jobs`

| Method | Path                   | Description                    |
|--------|------------------------|--------------------------------|
| POST   | `/jobs/analyze`        | Enqueue a graph analysis job   |
| GET    | `/jobs/{id}/status`    | Poll job status                |
| GET    | `/jobs/{id}/result`    | Retrieve job result            |

**Local imports:** `shared.jobqueue` (enqueue, get_status, get_result), `shared.schemas` (JobAnalyzeRequest).

---

## Shared Layer (`backend/shared/`)

Core utilities and infrastructure used by all routers and services.

### `shared/config.py` — Configuration

Loads all environment variables with defaults. Reads `.env` from two paths: `backend/.env` and `backend/shared/.env`.

**Key variable groups:**

| Group          | Variables                                                                |
|----------------|--------------------------------------------------------------------------|
| API metadata   | `API_TITLE`, `API_VERSION`, `API_DESCRIPTION`                           |
| CORS           | `CORS_ORIGINS` (localhost:5173-5177), `CORS_ORIGIN_REGEX`               |
| Server         | `HOST`, `PORT` (8000), `RELOAD`, `WS_MAX_SIZE`, `PORT_GRAPH/RAG/PROGRAM/AI` |
| Analysis       | `DEFAULT_MAX_FILES` (200), `MAX_FILES_CEILING` (1000), `MAX_CODE_DISPLAY_LENGTH` (4000), `PARSE_WORKERS`, `MAX_FILE_SIZE` (1MB) |
| Postgres       | `DATABASE_URL`                                                           |
| MongoDB        | `MONGODB_URI`, `MONGODB_DB`, `MONGODB_GENERATED_COLLECTION`, `GENERATED_CODE_MAX_BYTES` (5MB) |
| Milvus         | `MILVUS_URI`, `MILVUS_COLLECTION`, `EMBEDDING_DIM` (384)                |
| LLM providers  | `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `HUGGINGFACE_TOKEN`, `HUGGINGFACE_TOKEN0`, `HF_MODEL_ID`, `HF_EMBEDDING_MODEL_ID` |
| Ollama         | `OLLAMA_HOST`, `OLLAMA_MODEL` (qwen2.5-coder:3b)                        |
| Auth           | `CLERK_JWKS_URL`                                                         |
| Microservices  | `GRAPH_SVC_URL`, `AI_SVC_URL`, `RAG_SVC_URL`, `PROGRAM_SVC_URL`         |

### `shared/db.py` — PostgreSQL (asyncpg)

Async PostgreSQL client. No ORM — raw SQL via asyncpg.

**Connection management:**
- `get_pool()` — Lazy pool creation; auto-detects stale pools (event loop replacement) and recreates.
- `close_pool()` — Graceful shutdown.
- `_init_schema(pool)` — Runs all migration `.sql` files in `shared/migrations/` sorted by filename.

**Operations:**

| Function                      | Description                                              |
|-------------------------------|----------------------------------------------------------|
| `write_codebase_graph`        | Upserts nodes and edges for a codebase_id                |
| `read_codebase_graph`         | Loads all nodes + edges as React Flow format             |
| `list_analyses`               | Lists analyses (optionally by user_id)                   |
| `set_symbol_summary`          | Updates the AI summary for a single node                 |
| `write_program_graph`         | Stores program graph (JSONB nodes + edges)               |
| `read_program_graph`          | Loads program graph by program_id                        |
| `list_program_graphs_by_user` | Lists program graphs for a user                          |
| `set_program_node_summary`    | Updates summary for a node inside JSONB                  |
| `rag_query_keyword`           | ILIKE search on name, filepath, code, summary columns    |
| `get_graph_nodes_by_ids`      | Fetches nodes by list of node_ids                        |

### `shared/ai.py` — LLM Interaction (Ollama / HuggingFace)

Streaming LLM functions using `openai` SDK as client for both Ollama and HuggingFace Router.

**Provider priority:** Ollama (when `OLLAMA_HOST` is set) > HuggingFace Inference.

| Function                       | Description                                       |
|--------------------------------|---------------------------------------------------|
| `generate_explanation_stream`  | Streaming code explanation via chat completion     |
| `generate_explanation`         | Blocking code explanation                          |
| `generate_summary`             | Short one-line summary for a graph node            |
| `prefetch_explanations_sync`   | Batch parallel summary generation                  |
| `explain_graph`                | Batch async explain for multiple nodes             |
| `chat_stream`                  | Streaming conversational chat                      |
| `transcribe` / `transcribe_async` | Speech-to-text via HF Whisper                  |
| `synthesize` / `synthesize_async` | Text-to-speech via HF                           |

**Configuration:** Uses `openai.OpenAI` client against `router.huggingface.co/v1` (HF) or `{OLLAMA_HOST}/v1` (Ollama).

### `shared/auth.py` — Authentication

Clerk JWT verification via JWKS:
- `_get_jwks_client()` — Creates/caches `PyJWKClient` from `CLERK_JWKS_URL`.
- `get_current_user_optional(credentials)` — FastAPI dependency; returns Clerk user_id or None.
- `get_current_user(user_id)` — FastAPI dependency; raises 401 if not authenticated.

### `shared/parser.py` — Code Parsing

Multi-language tree-sitter parser:
- `UniversalParser` class with lazy-cached parser/query objects.
- **Supported:** Python, JavaScript, TypeScript/TSX, Java, Rust, C, C++, Go.
- `parse_file(filepath)` → list of dicts: `name`, `type`, `start_line`, `end_line`, `code`.

### `shared/crawler.py` — GitHub Crawler

Streams file contents from GitHub API without local clone:
- `GitHubCrawler` class with `stream_files()` async generator.
- Rate-limit aware with exponential backoff.

### `shared/ingest.py` — Code Ingestion

Handles codebase input:
- `clone_github_repo(url)` — Shallow git clone to temp directory.
- `process_upload(file)` — ZIP extraction with zip-slip protection, file count limit (10,000), size limit.

### `shared/embedding.py` — Embedding Generation

HuggingFace Inference API embeddings:
- `embed_text(text, api_key)` → float vector.
- Model: `HF_EMBEDDING_MODEL_ID` (default: `nomic-ai/nomic-embed-text-v1.5`).
- Dimension: `EMBEDDING_DIM` (default 384).
- Input truncation at 8,000 chars.

### `shared/milvus_service.py` — Vector Database

Milvus client for vector storage and ANN search:
- Collection schema: `id` (PK), `codebase_id`, `symbol_id`, `embedding` (FLOAT_VECTOR).
- `IVF_FLAT` index with inner product metric, nlist=128.
- `insert_embeddings` — Delete-then-insert by codebase.
- `search` — ANN search filtered by `codebase_id`.

### `shared/mongo_service.py` — MongoDB

Document storage for generated code:
- `save_generated_code(user_id, program_id, ...)` — Insert doc with artifacts (up to 5MB).
- `get_generated_code(generation_id)` — Fetch by ID.
- `list_generated_for_user(user_id, limit)` — List user's generations.

### `shared/llm_providers.py` — Multi-Provider LLM

Unified async completion interface:
- `completion(provider, messages, model, api_keys)` — Routes to OpenAI, Anthropic, or HuggingFace.
- BYOK (Bring Your Own Key) via `api_keys` parameter; falls back to server-side env vars.
- Default models: `gpt-4o-mini` (OpenAI), `claude-3-5-haiku-20241022` (Anthropic), `HF_MODEL_ID` (HuggingFace).

### `shared/narrator.py` — Linear Narrator

BFS-based codebase tour:
- `run_narration(ws, graph_data, ...)` — Walks nodes via BFS from entry point.
- `run_node_narration(ws, graph_data, node_id, ...)` — Single node deep-dive.
- Entry detection via entry_score and in/out-degree heuristics.

### `shared/narrator_graph.py` — LangGraph Narrator (Unused)

State-machine narrator built on LangGraph `StateGraph` with interrupt-based pause. Currently not imported by any router — `routers/narrator_ws.py` uses `shared/narrator.py` instead.

### `shared/rag_chain.py` — RAG Chain (Unused)

LangChain-based retrieval and conversational chat:
- `CodebaseRetriever` — Hybrid retriever (Postgres keyword + Milvus vector).
- `build_chat_chain()` — Conversational with LRU session history.
Currently not imported by any active router. `routers/rag.py` calls `shared.db` and `shared.milvus_service` directly.

### `shared/jobqueue.py` — In-Memory Job Queue

Thread-safe queue using Python `queue.Queue` + dict:
- `enqueue(job_type, payload)` → job_id (UUID string).
- `pop_job(timeout_sec)` — Blocking pop.
- `set_running`, `set_result`, `set_failed` — Lifecycle updates.
- `is_available()` — Always returns True (no external dependency).
- Jobs are **not persistent** — lost on restart.

### `shared/schemas.py` — Pydantic Models

Request/response schemas:
- `ExplainRequest` (code, context, callers, callees)
- `GithubRequest` (url)
- `ApiKeysBody` (openai, anthropic)
- `RagQueryRequest` (codebase_id, query, k, program_id, use_vector)
- `RagChunk`, `RagQueryResponse`
- `ProgramNodeInput`, `ProgramEdgeInput`, `ProgramGraphRequest`
- `ProgramSummarizeRequest` (program_id, provider, model, api_keys)
- `JobAnalyzeRequest` (path, url, max_files, codebase_id, user_id)
- `GenerateCodeRequest` (program_id, codebase_id, target_language, stack, provider, model, api_keys, user_id)

### `shared/state.py` — Shared State

In-memory singletons:
- `graph_cache: dict` — `{"graph": None}`, holds last analysis result.
- `get_parser()` — Lazy `UniversalParser` singleton.

---

## Graph Builder — The Core Engine (`graph/builder.py`)

The `GraphBuilder` class transforms a directory of source files into a structured dependency graph.

### Pipeline

```
Directory
    │
    ▼
_collect_files()          BFS file collection, skip IGNORED_DIRS
    │                     Respects MAX_FILE_SIZE and max_files limit
    ▼
_process_file()           tree-sitter parse per file
    │                     Extract functions, classes, entry blocks
    │                     Compute entry_score per node
    ▼
_create_edges()           Two-phase heuristic:
    │                     Phase 1: tokenize code, match symbol references
    │                              → CALLS / INSTANTIATES edges
    │                     Phase 2: extract external imports
    │                              → library blob nodes + USES edges
    ▼
_truncate_node_code_for_memory()
    │                     Post-edge code truncation to MAX_CODE_DISPLAY_LENGTH
    ▼
_run_reconciliation()     ReconciliationEngine classifies filepaths:
    │                     Layer 0 = root-level files
    │                     Layer 1 = their direct local imports
    │                     Layer 2 = everything else
    ▼
to_json()                 Serialize to React Flow format:
                          - Layer 0 nodes at top row
                          - Layer 1 nodes in grid below
                          - Layer 2 nodes in grid below that
                          - "reconciliation" key included in response
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

### Language-Specific Tokenizers

Tokenizers exist for: Python, JavaScript/TypeScript, Java, and a generic fallback. Each splits source code into identifier tokens for cross-reference matching during edge detection.

---

## Reconciliation Engine (`graph/reconciliation.py`)

The `ReconciliationEngine` surfaces the entry layer of a codebase before diving into subdirectory structure.

### Three-Layer Classification

| Layer | Contents                                        | Example                  |
|-------|-------------------------------------------------|--------------------------|
| **0** | Files directly in the repo root                 | `index.js`, `app.ts`    |
| **1** | Local files those root files directly import    | `routes/index.js`       |
| **2** | Everything else                                 | `models/user.js`        |

### Import Resolution

| Language | Relative imports             | Bare/absolute imports            |
|----------|------------------------------|----------------------------------|
| JS / TS  | `import from './routes'`, `require()`, dynamic `import()`, re-exports | Resolved against project root; `@scope/pkg` skipped |
| Python   | `from .module import`, `from ..pkg import` | `from models.user import`, `import config` via dotted→path |

An import is considered **local** only if the path resolves to an actual file in the collected file set. Unresolved imports are recorded separately (third-party/stdlib).

### Result: `ReconciliationSurface`

```python
surface.root_files          # ['index.js', 'middleware.js']
surface.direct_deps         # {'index.js': ['routes/index.js', 'config/db.js'], ...}
surface.unresolved          # {'index.js': ['express', 'mongoose'], ...}
surface.layer_map           # {'index.js': 0, 'routes/index.js': 1, 'models/user.js': 2}
surface.all_layer1_files    # set of all Layer-1 filepaths
surface.to_api_dict()       # serialised for frontend
```

### Integration with `GraphBuilder`

`GraphBuilder._run_reconciliation()` calls the engine after `_create_edges()` and caches the result. `to_json()` includes `surface.to_api_dict()` under the `"reconciliation"` key in the response payload.

---

## Jobs System (`jobs/handlers.py`)

Background job workers process queued tasks in the worker thread spawned by `main.py`.

### `graph_analyze` Job

1. Resolves local path or clones repo via `shared.ingest`.
2. Calls `build_graph_for()` to parse and build the graph.
3. Persists nodes and edges to Postgres via its own `asyncpg` connection (not shared pool, since it runs in a worker thread with its own event loop).
4. Auto-enqueues a follow-up `graph_explain` job.

### `graph_explain` Job

1. Loads graph nodes from the analysis result.
2. Batch-generates summaries for user-code nodes (skips libraries).
3. Uses `shared.ai.generate_summary` for each node.
4. Updates summaries individually in the DB.
5. Aborts early if all nodes in a batch fail (Ollama unresponsive).

### Job Dispatcher

`run_job(job_id, payload)` dispatches based on `payload["type"]`:
- `"graph_analyze"` → `_run_graph_analyze`
- `"graph_explain"` → `_run_graph_explain`
