# API Reference

Complete HTTP and WebSocket API for EzDocs.

---

## Base URLs

| Mode            | URL                          |
|-----------------|------------------------------|
| Monolith        | `http://localhost:8000`      |
| Gateway         | `http://localhost:8000`      |
| Graph service   | `http://localhost:8001`      |
| RAG service     | `http://localhost:8003`      |
| Program service | `http://localhost:8004`      |

---

## Authentication

Optional Clerk JWT authentication. When enabled, pass the JWT in the `Authorization` header:

```
Authorization: Bearer <clerk-jwt-token>
```

Backend verifies the token via JWKS (`CLERK_JWKS_URL`). Most endpoints work without auth in development. Auth is required for user-scoped operations (saved analyses, program graphs, generated code listing).

The frontend's `AuthRequestInterceptor` automatically attaches Clerk JWTs to all axios requests.

---

## REST Endpoints

### Health / Meta

#### `GET /health`

Health check endpoint.

**Response:**
```json
{"status": "ok"}
```

---

### AI / Explanation

#### `POST /explain`

Generate an AI explanation for a code snippet.

**Request:**
```json
{
  "code": "def fibonacci(n):\n    if n <= 1: return n\n    return fibonacci(n-1) + fibonacci(n-2)",
  "context": "optional context string",
  "callers": ["optional", "list", "of", "caller", "names"],
  "callees": ["optional", "list", "of", "callee", "names"]
}
```

**Response:** `200 OK`, streaming text explanation.

---

### Graph Analysis

#### `GET /analyze`

Analyze a local directory.

**Query Parameters:**

| Param         | Type   | Required | Default | Description                          |
|---------------|--------|----------|---------|--------------------------------------|
| `path`        | string | yes      |         | Absolute path to directory           |
| `max_files`   | int    | no       | 200     | Max files to analyze (ceiling: 1000) |
| `codebase_id` | string | no       | (auto)  | UUID for this analysis               |
| `user_id`     | string | no       |         | Clerk user ID for persistence        |

**Response:**
```json
{
  "nodes": [
    {
      "id": "abc123",
      "type": "ez",
      "data": {
        "label": "main",
        "type": "function",
        "filepath": "src/main.py",
        "start_line": 1,
        "end_line": 15,
        "code": "def main():...",
        "explanation": "Entry point that initializes the app",
        "entry_score": 150,
        "is_library": false
      },
      "position": {"x": 0, "y": 0}
    }
  ],
  "edges": [
    {
      "id": "e-abc123-def456",
      "source": "abc123",
      "target": "def456",
      "type": "ez",
      "label": "CALLS"
    }
  ],
  "reconciliation": {
    "root_files": ["main.py"],
    "direct_deps": {"main.py": ["config.py", "routes/index.py"]},
    "unresolved": {"main.py": ["fastapi", "uvicorn"]},
    "layer_map": {"main.py": 0, "config.py": 1, "models/user.py": 2}
  }
}
```

---

#### `POST /analyze/github`

Analyze a GitHub repository (clone-based).

**Request:**
```json
{
  "url": "https://github.com/owner/repo"
}
```

**Response:** Same format as `GET /analyze`.

---

#### `POST /analyze/upload`

Analyze an uploaded ZIP archive.

**Request:** `multipart/form-data` with `file` field (ZIP).

**Response:** Same format as `GET /analyze`.

---

#### `GET /files`

Get recursive file tree for a directory.

**Query Parameters:**

| Param  | Type   | Required | Description            |
|--------|--------|----------|------------------------|
| `path` | string | yes      | Absolute directory path|

**Response:**
```json
{
  "tree": [
    {
      "name": "src",
      "type": "directory",
      "children": [
        {"name": "main.py", "type": "file", "path": "src/main.py"}
      ]
    }
  ]
}
```

---

#### `GET /analyses`

List saved analyses from Postgres.

**Query Parameters:**

| Param    | Type   | Required | Default | Description                 |
|----------|--------|----------|---------|-----------------------------|
| `user_id`| string | no       |         | Filter by user              |
| `limit`  | int    | no       | 100     | Max results                 |

**Response:**
```json
[
  {
    "codebase_id": "uuid",
    "source_path": "https://github.com/owner/repo",
    "created_at": "2025-01-15T10:30:00+00:00"
  }
]
```

---

#### `GET /graph`

Load a persisted graph from Postgres.

**Query Parameters:**

| Param         | Type   | Required | Description           |
|---------------|--------|----------|-----------------------|
| `codebase_id` | string | yes      | Analysis UUID         |

**Response:** Same node/edge format as `GET /analyze` (without reconciliation).

---

### RAG (Retrieval-Augmented Generation)

#### `POST /rag/query`

Keyword + optional vector search over a codebase.

**Request:**
```json
{
  "codebase_id": "uuid",
  "query": "how does authentication work",
  "k": 10,
  "program_id": "optional-program-uuid",
  "use_vector": false
}
```

**Response:**
```json
{
  "chunks": [
    {
      "id": "node-uuid",
      "type": "symbol",
      "name": "verify_token",
      "filepath": "src/auth.py",
      "summary": "Verifies JWT token...",
      "code": "def verify_token(token):...",
      "content": null
    }
  ]
}
```

When `program_id` is provided, program graph nodes are also searched if keyword results are below `k`.

---

#### `POST /rag/index`

Generate embeddings for all graph nodes and store in Milvus.

**Query Parameters:**

| Param         | Type   | Required | Description           |
|---------------|--------|----------|-----------------------|
| `codebase_id` | string | yes      | Analysis UUID         |

**Response:**
```json
{
  "indexed": 42,
  "skipped": 5,
  "errors": 0
}
```

---

### Program Graphs & Code Generation

#### `POST /program`

Create or replace a program intent graph.

**Request:**
```json
{
  "program_id": "uuid",
  "nodes": [
    {"id": "1", "content": "JWT-based login/signup", "label": "User Auth", "order": 0}
  ],
  "edges": [
    {"source_id": "2", "target_id": "1"}
  ],
  "user_id": "clerk-user-id"
}
```

**Response:** `200 OK` with `{"ok": true}` or similar confirmation.

---

#### `GET /program`

Read a program graph.

**Query Parameters:**

| Param        | Type   | Required | Description         |
|--------------|--------|----------|---------------------|
| `program_id` | string | yes      | Program UUID        |

**Response:**
```json
{
  "nodes": [
    {"id": "1", "content": "...", "label": "User Auth", "summary": "...", "order": 0}
  ],
  "edges": [
    {"source_id": "2", "target_id": "1"}
  ]
}
```

---

#### `GET /program/list`

List programs for the authenticated user.

**Query Parameters:**

| Param   | Type | Required | Default | Description |
|---------|------|----------|---------|-------------|
| `limit` | int  | no       | 100     | Max results |

**Response:**
```json
[
  {"program_id": "uuid", "name": "My App", "created_at": "2025-01-15T10:30:00"}
]
```

Requires authentication (Clerk JWT).

---

#### `POST /program/summarize`

Generate LLM summaries for program nodes.

**Request:**
```json
{
  "program_id": "uuid",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_keys": {"openai": "sk-..."}
}
```

**Response:** Updated program graph with `summary` fields populated on each node.

---

#### `POST /generate/code`

Generate code from a program graph.

**Request:**
```json
{
  "program_id": "uuid",
  "codebase_id": "optional-uuid",
  "target_language": "python",
  "stack": "FastAPI + PostgreSQL",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_keys": {"openai": "sk-..."},
  "user_id": "clerk-user-id"
}
```

**Response:**
```json
{
  "generation_id": "uuid",
  "artifacts": {
    "src/main.py": "# Generated code...",
    "src/auth.py": "# More code..."
  }
}
```

---

#### `GET /generated/{generation_id}`

Retrieve generated code by ID from MongoDB.

**Response:**
```json
{
  "generation_id": "uuid",
  "program_id": "uuid",
  "artifacts": {"src/main.py": "..."},
  "created_at": "2025-01-15T10:30:00"
}
```

---

#### `GET /generated`

List generated code entries for a user.

**Query Parameters:**

| Param     | Type   | Required | Default | Description        |
|-----------|--------|----------|---------|--------------------|
| `user_id` | string | no       |         | Filter by user     |
| `limit`   | int    | no       | 50      | Max results        |

**Response:**
```json
[
  {"generation_id": "uuid", "program_id": "uuid", "created_at": "..."}
]
```

---

### Background Jobs

#### `POST /jobs/analyze`

Enqueue a background analysis job.

**Request:**
```json
{
  "path": "/absolute/path/to/codebase",
  "url": "https://github.com/owner/repo",
  "max_files": 200,
  "codebase_id": "optional-uuid",
  "user_id": "optional-clerk-id"
}
```

Provide either `path` or `url`, not both.

**Response:**
```json
{
  "job_id": "uuid"
}
```

---

#### `GET /jobs/{job_id}/status`

Poll job status.

**Response:**
```json
{
  "job_id": "uuid",
  "status": "running"
}
```

Status values: `queued`, `running`, `done`, `failed`.

---

#### `GET /jobs/{job_id}/result`

Get job result (available when status is `done`).

**Response:**
```json
{
  "job_id": "uuid",
  "result": {
    "codebase_id": "uuid",
    "node_count": 42,
    "edge_count": 67
  }
}
```

---

## WebSocket Endpoints

### `WS /ws/analyze/github`

Streaming GitHub analysis with real-time progress.

**Client → Server (first message):**
```json
{"url": "https://github.com/owner/repo"}
```

**Server → Client (progress updates):**
```json
{"type": "update", "message": "Fetching file tree..."}
{"type": "update", "message": "Parsing src/main.py (12/45 files)"}
```

**Server → Client (completion):**
```json
{
  "type": "complete",
  "graph": {
    "nodes": [...],
    "edges": [...]
  }
}
```

**Server → Client (error):**
```json
{"type": "error", "message": "Repository not found"}
```

---

### `WS /ws/explain`

Streaming code explanation over WebSocket.

**Client → Server:**
```json
{
  "code": "def foo(): ...",
  "context": "optional context",
  "callers": [],
  "callees": []
}
```

**Server → Client:** Streaming text chunks, then connection closes.

---

### `WS /ws/chat`

Streaming conversational chat.

**Client → Server:**
```json
{
  "message": "How does authentication work?",
  "codebase_id": "optional-uuid"
}
```

**Server → Client:** Streaming text chunks. A `\x01` byte signals end-of-stream. JSON objects with `error` field indicate errors.

---

### `WS /ws/narrate`

Interactive codebase tour narration.

**Client → Server (initial, optional):**
```json
{"codebase_id": "uuid"}
```

If `codebase_id` provided, loads graph from Postgres. Otherwise uses in-memory `graph_cache`.

**Server → Client frames:**

| Frame Type | Payload                                             | Description                    |
|------------|-----------------------------------------------------|--------------------------------|
| `focus`    | `{"type":"focus", "node_id":"abc", "label":"main"}` | Highlight a node on canvas     |
| `text`     | `{"type":"text", "chunk":"This function..."}`       | Streaming narration text chunk |
| `pause`    | `{"type":"pause"}`                                  | Waiting for user input         |
| `done`     | `{"type":"done"}`                                   | Tour completed                 |
| `error`    | `{"type":"error", "message":"..."}`                 | Error occurred                 |

**Client → Server (user actions):**

| Action    | Payload                                          | Description                   |
|-----------|--------------------------------------------------|-------------------------------|
| Continue  | `{"action":"continue"}`                          | Move to next node             |
| Question  | `{"action":"question", "text":"What does...?"}` | Ask about current node        |
| Focus     | `{"action":"focus", "node_id":"def456"}`         | Jump to a specific node       |

---

### `WS /ws/narrate/node`

Single-node deep-dive narration.

**Client → Server:**
```json
{
  "node_id": "abc123",
  "codebase_id": "uuid"
}
```

**Server → Client:** Same frame types as `/ws/narrate` (`focus`, `text`, `done`, `error`). Non-interactive — streams the full explanation then sends `done`.

---

## Error Responses

All REST endpoints return standard HTTP error codes:

| Code | Meaning                                    |
|------|--------------------------------------------|
| 400  | Bad request (validation error)             |
| 401  | Unauthorized (missing/invalid Clerk JWT)   |
| 404  | Resource not found                         |
| 422  | Unprocessable entity (Pydantic validation) |
| 500  | Internal server error                      |

Error response format:
```json
{
  "detail": "Error description"
}
```

---

## CORS Configuration

Development mode allows localhost origins on ports 5173–5177. Production can use `EZDOCS_CORS_ORIGIN_REGEX` for custom domain matching or `EZDOCS_CORS_ORIGINS` (comma-separated) for additional explicit origins.

```
Allowed methods: GET, POST, PUT, PATCH, DELETE, OPTIONS
Allowed headers: *
Credentials: true
```

A custom `PreflightCORSMiddleware` handles `OPTIONS` requests explicitly for compatibility with strict browser preflight checks.

---

## Middleware Stack (Monolith)

Applied in order (bottom of stack runs first):

| Middleware              | Purpose                                     |
|-------------------------|---------------------------------------------|
| `PreflightCORSMiddleware` | Explicit OPTIONS handling with CORS headers |
| `GZipMiddleware`        | Response compression (min 1,000 bytes)      |
| `CORSMiddleware`        | Standard CORS header injection              |
