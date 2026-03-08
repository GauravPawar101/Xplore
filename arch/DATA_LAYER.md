# Data Layer

## Database Overview

Xplore uses three databases, each for a distinct purpose:

| Database       | Purpose                        | Client Library | Port  |
|----------------|--------------------------------|----------------|-------|
| PostgreSQL 16  | Analyses, graphs, users, chat  | asyncpg        | 7998  |
| Milvus 2.4     | Vector embeddings for RAG      | pymilvus       | 19530 |
| MongoDB 7      | Generated code blobs           | pymongo        | 27017 |

Additionally, **Upstash Redis** (HTTP-based) serves as the background job queue.

---

## PostgreSQL Schema

All managed via SQL migration files in `backend/shared/migrations/`.

### Migration Files

| File                      | Description                                      |
|---------------------------|--------------------------------------------------|
| `001_init.sql`            | Core schema: users, analyses, graph_nodes, graph_edges, program_graphs |
| `002_entry_score.sql`     | Adds `entry_score INT` to graph_nodes            |
| `003_chat.sql`            | Creates `chat_messages` table                    |

Migrations run automatically on startup via `db.run_migrations()`. A separate migration (`002_init.sql`) adds `is_library BOOLEAN` to `graph_nodes`.

### Tables

#### `users`

| Column       | Type         | Notes                |
|--------------|--------------|----------------------|
| id           | UUID (PK)    | Auto-generated       |
| clerk_id     | VARCHAR      | Unique, from Clerk   |
| email        | VARCHAR      |                      |
| created_at   | TIMESTAMPTZ  | Default now()        |

#### `analyses`

| Column       | Type         | Notes                            |
|--------------|--------------|----------------------------------|
| id           | UUID (PK)    | Also used as `codebase_id`      |
| user_id      | UUID (FK)    | References users.id, nullable   |
| name         | VARCHAR      | Display name for the analysis   |
| source_path  | VARCHAR      | Original path/URL analyzed      |
| created_at   | TIMESTAMPTZ  |                                  |

#### `graph_nodes`

| Column       | Type         | Notes                                         |
|--------------|--------------|-----------------------------------------------|
| id           | UUID (PK)    |                                               |
| analysis_id  | UUID (FK)    | References analyses.id                        |
| node_id      | VARCHAR      | Application-level node identifier             |
| name         | VARCHAR      | Symbol name (function/class)                  |
| type         | VARCHAR      | `function`, `class`, `entry_block`            |
| filepath     | VARCHAR      | Relative path within the codebase             |
| start_line   | INT          |                                               |
| end_line     | INT          |                                               |
| code         | TEXT         | Full source code of the symbol                |
| summary      | TEXT         | AI-generated one-line summary                 |
| entry_score  | INT          | Entrypoint detection score (0–170)            |
| is_library   | BOOLEAN      | True for external package blob nodes          |

**Indexes:**
- `pg_trgm` GIN index on `name` — Fast trigram-based ILIKE search
- `pg_trgm` GIN index on `code` — Keyword search inside code bodies
- B-tree index on `(analysis_id, node_id)` — Fast lookup by codebase + node

#### `graph_edges`

| Column       | Type         | Notes                              |
|--------------|--------------|------------------------------------|
| id           | UUID (PK)    |                                    |
| analysis_id  | UUID (FK)    | References analyses.id             |
| source_id    | VARCHAR      | Source node_id                     |
| target_id    | VARCHAR      | Target node_id                     |
| edge_type    | VARCHAR      | `CALLS`, `INSTANTIATES`, `USES`   |

#### `program_graphs`

| Column       | Type         | Notes                              |
|--------------|--------------|------------------------------------|
| id           | UUID (PK)    |                                    |
| user_id      | UUID (FK)    | Nullable                           |
| name         | VARCHAR      |                                    |
| graph_data   | JSONB        | Full program graph (nodes + edges) |
| created_at   | TIMESTAMPTZ  |                                    |
| updated_at   | TIMESTAMPTZ  |                                    |

#### `chat_messages`

| Column       | Type         | Notes                              |
|--------------|--------------|------------------------------------|
| id           | UUID (PK)    |                                    |
| session_id   | VARCHAR      | Groups messages into conversations |
| user_id      | UUID         | Nullable                           |
| codebase_id  | UUID         | References analyses.id             |
| role         | VARCHAR      | `user` or `assistant`              |
| content      | TEXT         | Message text                       |
| created_at   | TIMESTAMPTZ  |                                    |

---

## PostgreSQL Access Layer (`shared/db.py`)

### Connection Management

```python
get_pool()     # Returns/creates asyncpg connection pool
close_pool()   # Closes the pool on shutdown
run_migrations()  # Runs all .sql files in migrations/ folder
```

### Key Operations

| Function                  | Description                                              |
|---------------------------|----------------------------------------------------------|
| `persist_analysis`        | Upserts an analysis row (by source_path or creates new) |
| `persist_nodes_edges`     | Bulk-writes nodes and edges using asyncpg COPY           |
| `load_graph`              | Loads all nodes + edges for a codebase_id                |
| `list_analyses`           | Lists analyses for a user, newest first                  |
| `get_node_code`           | Fetches code column for a single node                    |
| `rag_query_keyword`       | Trigram ILIKE search on node name + code columns         |
| `save_chat_message`       | Inserts a chat message row                               |
| `load_chat_history`       | Loads messages by session_id, ordered by timestamp       |
| `update_node_summary`     | Updates the AI summary for a single node                 |
| `batch_update_summaries`  | Updates summaries for many nodes in one transaction      |

---

## Milvus Vector Database

### Collection Schema

Collection name: configurable via `MILVUS_COLLECTION` (default: `xplore_embeddings`)

| Field         | Type          | Notes                                       |
|---------------|---------------|---------------------------------------------|
| id            | VARCHAR (PK)  | Format: `{codebase_id}::{symbol_id}`       |
| codebase_id   | VARCHAR       | Partition-like filter field                  |
| symbol_id     | VARCHAR       | References graph_nodes.node_id              |
| embedding     | FLOAT_VECTOR  | Dimension from `EMBEDDING_DIM` (default 768)|

### Index Configuration

- **Type:** `IVF_FLAT`
- **Metric:** Inner Product (`IP`)
- **nlist:** 128

### Access Layer (`shared/milvus_service.py`)

| Function              | Description                                          |
|-----------------------|------------------------------------------------------|
| `is_available()`      | Checks if Milvus is reachable                       |
| `insert_embeddings`   | Upserts vectors (delete codebase → insert fresh)    |
| `search`              | ANN search filtered by codebase_id, returns top-k   |
| `delete_codebase`     | Removes all vectors for a codebase                  |

### Embedding Pipeline

```
Graph Node (code text)
       │
       ▼
shared/embedding.py → embed_text()
       │
       │  POST /api/embeddings to Ollama
       │  Model: nomic-embed-text
       │  Input truncated to 8000 chars
       ▼
Float vector (768 dims)
       │
       ▼
milvus_service.insert_embeddings()
       │
       ▼
Milvus Collection (IVF_FLAT index)
```

---

## MongoDB

### Purpose

Stores generated code artifacts from the program-to-code pipeline. Each document can be up to 5MB.

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

| Function                | Description                                |
|-------------------------|--------------------------------------------|
| `save_generated_code`   | Saves artifacts dict, returns generation_id|
| `get_generated_code`    | Retrieves by generation_id                 |
| `list_generated_for_user` | Lists entries for a user, newest first   |

---

## Upstash Redis (Job Queue)

### Purpose

Background job queue for long-running tasks like codebase analysis and batch explanation generation.

### Key-Value Schema

| Key Pattern           | Type   | Content              | TTL    |
|-----------------------|--------|----------------------|--------|
| `job:{id}:status`     | STRING | pending/running/done/failed | 3 days |
| `job:{id}:payload`    | STRING | JSON job payload     | 3 days |
| `job:{id}:result`     | STRING | JSON result          | 3 days |
| `xplore:job_queue`    | LIST   | Queue of job IDs     | —      |

### Access Layer (`shared/jobqueue.py`)

| Function      | Description                                    |
|---------------|------------------------------------------------|
| `enqueue`     | Creates job, pushes to queue list              |
| `pop_job`     | RPOP one job from queue (non-blocking)         |
| `get_status`  | Read job status                                |
| `get_result`  | Read job result                                |
| `set_running` | Mark job as running                            |
| `set_result`  | Mark job as done, store result                 |
| `set_failed`  | Mark job as failed, store error                |

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
│  GraphBuilder    │  parse → build graph → detect entries
│  (graph/builder) │
└────────┬────────┘
         │ NetworkX graph
         ▼
┌─────────────────┐
│  to_json()       │  Serialize to React Flow format
└────────┬────────┘
         │
    ┌────┴────────────────┐
    ▼                     ▼
┌──────────┐       ┌──────────┐
│ Frontend │       │ Postgres │  persist_nodes_edges()
│ (render) │       │ (store)  │
└──────────┘       └──────────┘
```

### RAG Indexing Flow

```
POST /rag/index { codebase_id }
        │
        ▼
Load all non-library nodes from Postgres
        │
        ▼
For each node (bounded concurrency):
  embed_text(node.code) → Ollama → 768-dim vector
        │
        ▼
milvus_service.insert_embeddings(codebase_id, ids, vectors)
        │
        ▼
Milvus collection updated with IVF_FLAT index
```

### RAG Query Flow

```
POST /rag/query { codebase_id, query, session_id? }
        │
        ├──────────────────────────┐
        ▼                          ▼
┌──────────────┐           ┌──────────────┐
│ Postgres     │           │ Milvus       │
│ ILIKE search │           │ ANN search   │
│ (pg_trgm)   │           │ (embed query)│
└──────┬───────┘           └──────┬───────┘
       │                          │
       └────────┬─────────────────┘
                ▼
         Deduplicate by chunk ID
                │
                ▼
         Top-k results returned
                │
        (if session_id provided)
                ▼
         LangChain chat chain
         with session history
```

### Code Generation Flow

```
POST /generate/code { program_nodes, program_edges, codebase_id? }
        │
        ├── (if codebase_id) ── RAG retrieval for context
        │
        ▼
Build prompt: program intents + RAG context
        │
        ▼
LLM completion (OpenAI / Anthropic / HuggingFace)
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
