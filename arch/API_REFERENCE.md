# API Reference

Complete HTTP and WebSocket API for Xplore.

---

## Base URLs

| Mode          | URL                          |
|---------------|------------------------------|
| Monolith      | `http://localhost:8000`      |
| Gateway       | `http://localhost:8000`      |
| Graph service | `http://localhost:8001`      |
| RAG service   | `http://localhost:8003`      |
| Program svc   | `http://localhost:8004`      |

---

## Authentication

Optional Clerk JWT authentication. When enabled, pass the JWT in the `Authorization` header:

```
Authorization: Bearer <clerk-jwt-token>
```

Most endpoints work without auth in development. Auth is required for user-scoped operations (saved analyses, program graphs, generated code listing).

---

## REST Endpoints

### AI / Explanation

#### `POST /explain`

Generate an AI explanation for a code snippet.

**Request:**
```json
{
  "code": "def fibonacci(n):\n    if n <= 1: return n\n    return fibonacci(n-1) + fibonacci(n-2)",
  "language": "python"
}
```

**Response:** Streamed text explanation.

---

### Graph Analysis

#### `GET /analyze`

Analyze a local directory.

**Query Parameters:**

| Param     | Type   | Required | Description                          |
|-----------|--------|----------|--------------------------------------|
| `path`    | string | yes      | Absolute path to directory           |
| `explain` | bool   | no       | Generate AI summaries (default false)|
| `persist` | bool   | no       | Save to Postgres (default false)     |

**Response:**
```json
{
  "graph": {
    "nodes": [
      {
        "id": "abc123",
        "type": "ez",
        "data": {
          "label": "main",
          "symbolType": "function",
          "filepath": "src/main.py",
          "startLine": 1,
          "endLine": 15,
          "code": "def main():...",
          "summary": "Entry point that initializes the app",
          "entry_score": 150,
          "isEntry": true,
          "isLibrary": false
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
        "data": {"edgeType": "CALLS"}
      }
    ]
  },
  "codebase_id": "uuid-string"
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

**Response:** Same as `GET /analyze`.

---

#### `POST /analyze/upload`

Analyze an uploaded ZIP archive.

**Request:** `multipart/form-data` with `file` field (ZIP).

**Response:** Same as `GET /analyze`.

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

List saved analyses for the authenticated user.

**Response:**
```json
[
  {
    "id": "uuid",
    "name": "my-project",
    "source_path": "https://github.com/owner/repo",
    "created_at": "2025-01-15T10:30:00Z",
    "node_count": 42
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

**Response:** Same graph format as `GET /analyze`.

---

#### `GET /graph/code`

Fetch source code for a single node (lazy loading).

**Query Parameters:**

| Param         | Type   | Required | Description           |
|---------------|--------|----------|-----------------------|
| `codebase_id` | string | yes      | Analysis UUID         |
| `node_id`     | string | yes      | Graph node ID         |

**Response:**
```json
{
  "code": "def fibonacci(n):\n    ..."
}
```

---

### RAG (Retrieval-Augmented Generation)

#### `POST /rag/query`

Hybrid keyword + vector search over a codebase.

**Request:**
```json
{
  "codebase_id": "uuid",
  "query": "how does authentication work",
  "top_k": 5,
  "session_id": "optional-session-uuid"
}
```

**Response:**
```json
{
  "results": [
    {
      "chunk_id": "node-uuid",
      "name": "verify_token",
      "filepath": "src/auth.py",
      "code": "def verify_token(token):...",
      "score": 0.85
    }
  ],
  "answer": "Authentication is handled by..."
}
```

The `answer` field is populated when `session_id` is provided, using the conversational chat chain.

---

#### `POST /rag/index`

Generate embeddings for all graph nodes and store in Milvus.

**Request:**
```json
{
  "codebase_id": "uuid"
}
```

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
  "name": "My App",
  "nodes": [
    {"id": "1", "label": "User Auth", "description": "JWT login/signup"}
  ],
  "edges": [
    {"source": "2", "target": "1"}
  ]
}
```

**Response:**
```json
{
  "id": "uuid",
  "name": "My App",
  "created_at": "2025-01-15T10:30:00Z"
}
```

---

#### `GET /program`

Read a program graph.

**Query Parameters:**

| Param  | Type   | Required | Description    |
|--------|--------|----------|----------------|
| `id`   | string | yes      | Program UUID   |

**Response:** Full program graph JSON.

---

#### `GET /program/list`

List programs for the authenticated user.

**Response:**
```json
[
  {"id": "uuid", "name": "My App", "created_at": "...", "updated_at": "..."}
]
```

---

#### `POST /program/summarize`

Generate LLM summaries for program nodes.

**Request:**
```json
{
  "nodes": [
    {"id": "1", "label": "User Auth", "description": "JWT login/signup"}
  ],
  "provider": "openai",
  "api_keys": {"openai": "sk-..."}
}
```

**Response:**
```json
{
  "nodes": [
    {"id": "1", "label": "User Auth", "summary": "Handles JWT-based authentication..."}
  ]
}
```

---

#### `POST /generate/code`

Generate code from a program graph.

**Request:**
```json
{
  "program_nodes": [...],
  "program_edges": [...],
  "codebase_id": "optional-uuid",
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_keys": {"openai": "sk-..."}
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

Retrieve generated code by ID.

**Response:**
```json
{
  "generation_id": "uuid",
  "program_id": "uuid",
  "artifacts": {"src/main.py": "..."},
  "created_at": "..."
}
```

---

#### `GET /generated`

List generated code entries for the authenticated user.

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
  "url": "https://github.com/owner/repo",
  "path": null
}
```

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

Status values: `pending`, `running`, `done`, `failed`.

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

### `WS /ws/narrate`

Interactive codebase tour narration.

**Client → Server (initial, optional):**
```json
{"codebase_id": "uuid"}
```

If `codebase_id` provided, loads graph from Postgres. Otherwise uses in-memory `graph_cache`.

**Server → Client frames:**

| Frame Type | Payload                                        | Description                      |
|------------|------------------------------------------------|----------------------------------|
| `focus`    | `{"type":"focus", "node_id":"abc", "label":"main"}` | Highlight a node on the canvas |
| `text`     | `{"type":"text", "chunk":"This function..."}`  | Streaming narration text chunk   |
| `pause`    | `{"type":"pause"}`                             | Waiting for user input           |
| `done`     | `{"type":"done"}`                              | Tour completed                   |
| `error`    | `{"type":"error", "message":"..."}`            | Error occurred                   |

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
| 413  | Payload too large (ZIP > 500MB)            |
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

All origins allowed in development (`allow_origins=["*"]`). In production, configure via environment or middleware settings.

Allowed methods: `GET`, `POST`, `PUT`, `DELETE`, `OPTIONS`
Allowed headers: `*`
Credentials: `true`

---

## Middleware

| Middleware    | Purpose                                 |
|---------------|-----------------------------------------|
| CORSMiddleware| Cross-origin request handling           |
| GZipMiddleware| Response compression (min 500 bytes)    |
