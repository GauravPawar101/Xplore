# Data Layer

## Database Overview

EzDocs uses three databases, each for a distinct purpose:

| Database       | Purpose                              | Client Library | Port  |
|----------------|--------------------------------------|----------------|-------|
| PostgreSQL 16  | Analyses, graphs, programs, chat     | asyncpg        | 7998 (host) → 5432 (container) |
| Milvus 2.4     | Vector embeddings for RAG            | pymilvus       | 19530 |
| MongoDB 7      | Generated code blobs                 | pymongo        | 27017 |

---

## PostgreSQL Schema

All managed via SQL migration files in `backend/shared/migrations/`. Migrations run automatically on startup via `db._init_schema()` which sorts `.sql` files by filename and executes each statement.

### Migration Files

| File                  | Description                                                    |
|-----------------------|----------------------------------------------------------------|
| `001_init.sql`        | Core schema: users, analyses, graph_nodes, graph_edges, program_graphs |
| `002_entry_score.sql` | Adds `entry_score INT DEFAULT 0` to graph_nodes               |
| `002_init.sql`        | Adds `is_library BOOLEAN DEFAULT FALSE` to graph_nodes         |
| `003_chat.sql`        | Creates `chat_messages` table                                  |

**Note:** There are two files with the `002` prefix. Since `_init_schema` sorts by filename, `002_entry_score.sql` runs before `002_init.sql` (alphabetical order). They modify different columns so there is no conflict.

### Tables

#### `users`

```sql
CREATE TABLE IF NOT EXISTS users (
    id          TEXT PRIMARY KEY,
    email       TEXT,
    name        TEXT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

#### `analyses`

```sql
CREATE TABLE IF NOT EXISTS analyses (
    codebase_id TEXT PRIMARY KEY,
    user_id     TEXT,
    source_path TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

`codebase_id` is the primary identifier — both PK and the foreign key used by graph_nodes/graph_edges.

#### `graph_nodes`

```sql
CREATE TABLE IF NOT EXISTS graph_nodes (
    codebase_id TEXT     NOT NULL,
    node_id     TEXT     NOT NULL,
    name        TEXT     NOT NULL,
    type        TEXT     NOT NULL DEFAULT 'function',
    filepath    TEXT     NOT NULL DEFAULT '',
    start_line  INT      NOT NULL DEFAULT 0,
    end_line    INT      NOT NULL DEFAULT 0,
    code        TEXT,
    summary     TEXT,
    entry_score INT      NOT NULL DEFAULT 0,
    is_library  BOOLEAN  NOT NULL DEFAULT FALSE,
    PRIMARY KEY (codebase_id, node_id)
);
```

| Column       | Type    | Notes                                         |
|--------------|---------|-----------------------------------------------|
| codebase_id  | TEXT    | References analyses.codebase_id               |
| node_id      | TEXT    | Application-level node identifier             |
| name         | TEXT    | Symbol name (function/class)                  |
| type         | TEXT    | `function`, `class`, `entry_block`            |
| filepath     | TEXT    | Relative path within the codebase             |
| start_line   | INT    | Start line in source file                      |
| end_line     | INT    | End line in source file                        |
| code         | TEXT    | Source code (truncated to 10,000 chars in DB)  |
| summary      | TEXT    | AI-generated summary (truncated to 15,000 chars) |
| entry_score  | INT    | Entrypoint detection score (0–170)             |
| is_library   | BOOLEAN | True for external package blob nodes          |

**Indexes:**
- `idx_graph_nodes_codebase` on `(codebase_id)`
- `idx_graph_nodes_name` on `(name)`
- `idx_graph_nodes_library` on `(codebase_id, is_library)`

#### `graph_edges`

```sql
CREATE TABLE IF NOT EXISTS graph_edges (
    codebase_id TEXT NOT NULL,
    source_id   TEXT NOT NULL,
    target_id   TEXT NOT NULL,
    edge_type   TEXT NOT NULL DEFAULT 'CALLS',
    PRIMARY KEY (codebase_id, source_id, target_id)
);
```

| Column       | Type    | Notes                              |
|--------------|---------|------------------------------------|
| codebase_id  | TEXT    | References analyses.codebase_id    |
| source_id    | TEXT    | Source node_id                     |
| target_id    | TEXT    | Target node_id                     |
| edge_type    | TEXT    | `CALLS`, `INSTANTIATES`, `USES`   |

**Index:** `idx_graph_edges_codebase` on `(codebase_id)`

#### `program_graphs`

```sql
CREATE TABLE IF NOT EXISTS program_graphs (
    program_id TEXT PRIMARY KEY,
    user_id    TEXT     NOT NULL DEFAULT '',
    name       TEXT,
    nodes      JSONB    NOT NULL DEFAULT '[]',
    edges      JSONB    NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

| Column       | Type    | Notes                              |
|--------------|---------|------------------------------------|
| program_id   | TEXT    | Primary key                        |
| user_id      | TEXT    | Owner user ID                      |
| name         | TEXT    | Display name                       |
| nodes        | JSONB   | Array of node objects (id, content, label, order, summary) |
| edges        | JSONB   | Array of edge objects (source_id, target_id) |
| created_at   | TIMESTAMPTZ |                                |

**Index:** `idx_program_graphs_user` on `(user_id)`

#### `chat_messages`

```sql
CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    user_id     TEXT        NOT NULL DEFAULT '',
    codebase_id TEXT,
    role        TEXT        NOT NULL,   -- 'user' | 'assistant'
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
```

**Index:** `chat_messages_session_idx` on `(session_id, created_at)`

**Note:** This table is defined in migrations but no backend code currently reads or writes to it. It appears reserved for future persistent chat history.

---

## PostgreSQL Access Layer (`shared/db.py`)

### Connection Management

```python
get_pool()      # Lazy asyncpg pool (min_size=1, max_size=10, command_timeout=60)
close_pool()    # Graceful shutdown
_init_schema()  # Runs all .sql migration files in sorted order
```

The pool auto-detects stale event loops (e.g., after uvicorn reload) and recreates itself.

### Key Operations

| Function                      | Description                                              |
|-------------------------------|----------------------------------------------------------|
| `write_codebase_graph`        | INSERT/UPSERT nodes and edges; optionally clears first   |
| `read_codebase_graph`         | Loads nodes + edges as React Flow format dict             |
| `list_analyses`               | Lists analyses, optionally filtered by user_id            |
| `set_symbol_summary`          | Updates summary for a single graph node                   |
| `write_program_graph`         | INSERT/UPSERT program graph (JSONB nodes + edges)         |
| `read_program_graph`          | Loads program graph by program_id                         |
| `list_program_graphs_by_user` | Lists program graphs for a user                           |
| `set_program_node_summary`    | Updates summary for one node in a JSONB array             |
| `rag_query_keyword`           | ILIKE search on name, filepath, code, summary; optional program node search |
| `get_graph_nodes_by_ids`      | Fetch graph nodes by codebase_id and list of node_ids     |

### Write Pattern

`write_codebase_graph` uses individual `INSERT ... ON CONFLICT DO UPDATE` statements per node/edge, not `COPY`. The `jobs/handlers.py` worker uses its own direct `asyncpg` connection (not the shared pool) for persistence, since it runs in a separate thread with its own event loop.

---

## Milvus Vector Database

### Collection Schema

Collection name: configurable via `MILVUS_COLLECTION` (default: `ezdocs_graph_embeddings`)

| Field         | Type          | Notes                                       |
|---------------|---------------|---------------------------------------------|
| id            | VARCHAR (PK)  | Format: `{codebase_id}::{symbol_id}`       |
| codebase_id   | VARCHAR       | Partition-like filter field                  |
| symbol_id     | VARCHAR       | References graph_nodes.node_id              |
| embedding     | FLOAT_VECTOR  | Dimension from `EMBEDDING_DIM` (default 384)|

### Index Configuration

- **Type:** `IVF_FLAT`
- **Metric:** Inner Product (`IP`)
- **nlist:** 128

### Access Layer (`shared/milvus_service.py`)

| Function              | Description                                          |
|-----------------------|------------------------------------------------------|
| `_connect()`          | Lazy Milvus connection                               |
| `is_available()`      | Checks if Milvus is reachable                       |
| `_ensure_collection`  | Creates collection with schema if not exists         |
| `insert_embeddings`   | Delete existing → Insert fresh vectors for codebase  |
| `search`              | ANN search filtered by codebase_id, returns top-k   |
| `delete_codebase`     | Removes all vectors for a codebase                  |

### Embedding Pipeline

```
Graph Node (name + code text)
       │
       ▼
shared/embedding.py → embed_text()
       │
       │  POST to HuggingFace Inference API
       │  Model: nomic-ai/nomic-embed-text-v1.5
       │  Input truncated to 8000 chars
       ▼
Float vector (384 dims default)
       │
       │  _ensure_dim() pads/truncates to EMBEDDING_DIM
       ▼
milvus_service.insert_embeddings()
       │
       ▼
Milvus Collection (IVF_FLAT index)
```

---

## MongoDB

### Purpose

Stores generated code artifacts from the program-to-code pipeline. Each document can be up to `GENERATED_CODE_MAX_BYTES` (default 5MB).

### Configuration

| Setting                        | Default                      | Env Var                          |
|--------------------------------|------------------------------|----------------------------------|
| Connection URI                 | `mongodb://localhost:27017`  | `MONGODB_URI`                    |
| Database name                  | `ezdocs`                     | `MONGODB_DB`                     |
| Collection name                | `generated_code`             | `MONGODB_GENERATED_COLLECTION`   |
| Max artifact size              | 5MB                          | `EZDOCS_GENERATED_CODE_MAX_BYTES`|

### Document Structure

```json
{
  "_id": "ObjectId",
  "generation_id": "uuid-string",
  "user_id": "uuid-string",
  "program_id": "uuid-string",
  "artifacts": {
    "src/main.py": "# generated code...",
    "src/utils.py": "# more code..."
  },
  "created_at": "ISO datetime"
}
```

### Access Layer (`shared/mongo_service.py`)

| Function                  | Description                                |
|---------------------------|--------------------------------------------|
| `_get_client()`           | Lazy pymongo client creation               |
| `is_available()`          | Checks if MongoDB is reachable             |
| `_artifact_size(artifacts)` | Computes total size of artifacts dict    |
| `save_generated_code`     | Saves artifacts dict, returns generation_id|
| `get_generated_code`      | Retrieves by generation_id                 |
| `list_generated_for_user` | Lists entries for a user, newest first     |

---

## In-Memory Job Queue

### Purpose

Background job queue for long-running tasks (codebase analysis, batch AI explanation). Uses Python's thread-safe `queue.Queue` — no external service required.

### Storage

| Component       | Type                                    | Content                                   |
|-----------------|-----------------------------------------|-------------------------------------------|
| `_job_queue`    | `queue.Queue`                           | Queue of `(job_id, payload)` tuples       |
| `_job_store`    | `dict` (protected by `threading.Lock`)  | `job_id → { status, payload, result, error, created_at }` |

### Access Layer (`shared/jobqueue.py`)

| Function         | Description                                    |
|------------------|------------------------------------------------|
| `is_available()` | Always returns `True`                          |
| `enqueue`        | Creates job with UUID, pushes to queue         |
| `pop_job`        | Blocking pop with timeout (default 2s)         |
| `get_status`     | Read job status (`queued`, `running`, `done`, `failed`) |
| `get_result`     | Read job result dict                           |
| `set_running`    | Mark job as running                            |
| `set_result`     | Mark job as done, store result                 |
| `set_failed`     | Mark job as failed, store error                |

**Important:** Jobs are not persistent. All queued/running/completed jobs are lost on process restart.

---

## Data Flow Diagrams

### Analysis & Persistence Flow

```
User submits URL/path/ZIP
        │
        ▼
┌─────────────────┐
│  Ingest Layer    │  clone_github_repo() / process_upload()
│  (shared/ingest) │
└────────┬────────┘
         │ directory path
         ▼
┌─────────────────┐
│  GraphBuilder    │  parse → edges → reconciliation
│  (graph/builder) │
└────────┬────────┘
         │ nodes + edges + reconciliation surface
         ▼
┌─────────────────┐
│  to_json()       │  Serialize to React Flow format
└────────┬────────┘
         │
    ┌────┴────────────────┐
    ▼                     ▼
┌──────────┐       ┌──────────┐
│ Frontend │       │ Postgres │  write_codebase_graph()
│ (render) │       │ (store)  │
└──────────┘       └──────────┘
```

### RAG Indexing Flow

```
POST /rag/index { codebase_id }
        │
        ▼
Load all non-library graph_nodes from Postgres
        │
        ▼
For each node (bounded concurrency):
  embed_text(node.name + code) → HF Inference → float vector
        │
        ▼
milvus_service.insert_embeddings(codebase_id, ids, vectors)
        │
        ▼
Milvus collection updated with IVF_FLAT index
```

### RAG Query Flow

```
POST /rag/query { codebase_id, query, k, use_vector? }
        │
        ├──── Always ────────────────┐
        ▼                            ▼ (if use_vector)
┌──────────────┐           ┌──────────────┐
│ Postgres     │           │ Milvus       │
│ ILIKE search │           │ ANN search   │
│ name, fp,    │           │ (embed query)│
│ code, summary│           │              │
└──────┬───────┘           └──────┬───────┘
       │                          │
       └────────┬─────────────────┘
                ▼
         Merge + deduplicate by node ID
                │
                ▼
         Return top-k RagChunk list
```

### Code Generation Flow

```
POST /generate/code { program_id, codebase_id?, provider, ... }
        │
        ▼
Load program graph from Postgres
        │
        ├── (if codebase_id) ── RAG retrieval for context
        │
        ▼
Build prompt: program intents + RAG context + target stack
        │
        ▼
llm_providers.completion(provider, messages, model, api_keys)
        │
        ▼
Parse output → extract FILE: blocks → artifacts dict
        │
        ▼
MongoDB: save_generated_code()
        │
        ▼
Return { generation_id, artifacts }
```
