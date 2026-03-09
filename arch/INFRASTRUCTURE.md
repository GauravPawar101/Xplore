# Infrastructure — Docker, CI/CD, Deployment

## Docker Setup

### Primary Compose File (`docker-compose.yml`)

The main compose file defines all 8 services — 4 infrastructure + 4 application microservices:

```yaml
services:
  # Infrastructure
  postgres:     # PostgreSQL 16.4 Alpine
  mongo:        # MongoDB 7
  milvus:       # Milvus 2.4.0 standalone
  ollama:       # Ollama (latest)

  # Application microservices
  gateway:      # Port 8000 — reverse proxy + AI/narrator
  graph:        # Port 8001 — analysis, file explorer
  rag:          # Port 8003 — embeddings, search
  program:      # Port 8004 — intent graphs, code gen
```

#### Infrastructure Services

| Service    | Image                       | Port(s)       | Volume            | Healthcheck               |
|------------|-----------------------------|---------------|-------------------|---------------------------|
| postgres   | `postgres:16.4-alpine`      | 7998 → 5432   | `ezdocs_pgdata`   | `pg_isready -U postgres -d ezdocs` |
| mongo      | `mongo:7`                   | 27017 → 27017 | `ezdocs_mongo`    | `mongosh --eval "db.adminCommand('ping')"` |
| milvus     | `milvusdb/milvus:v2.4.0`   | 19530, 9091   | `ezdocs_milvus`   | None                      |
| ollama     | `ollama/ollama:latest`      | 11434 → 11434 | `ezdocs_ollama`   | None                      |

Container names: `ezdocs-postgres`, `ezdocs-mongo`, `ezdocs-milvus`, `ezdocs-ollama`.

#### Application Microservices

All four share the same Docker image (`ezdocs-backend`) built from `backend/Dockerfile`, and share `env_file: ./backend/.env`.

| Service  | Container     | Port | Command                                    | Depends On                           |
|----------|---------------|------|--------------------------------------------|--------------------------------------|
| gateway  | ezdocs-gateway| 8000 | `python gateway.py`                        | graph, rag, program (service_started)|
| graph    | ezdocs-graph  | 8001 | `uvicorn graph.app:app --host 0.0.0.0 --port 8001` | postgres (service_healthy)  |
| rag      | ezdocs-rag    | 8003 | `uvicorn rag.app:app --host 0.0.0.0 --port 8003`   | postgres (healthy), milvus (started) |
| program  | ezdocs-program| 8004 | `uvicorn program.app:app --host 0.0.0.0 --port 8004`| postgres (healthy), mongo (healthy)  |

**Environment overrides** for all microservices:
```
DATABASE_URL=postgresql://postgres:postgres@postgres:5432/ezdocs
MONGODB_URI=mongodb://mongo:27017
MILVUS_URI=http://milvus:19530
```

**Gateway additional env:**
```
EZDOCS_GRAPH_SVC_URL=http://graph:8001
EZDOCS_RAG_SVC_URL=http://rag:8003
EZDOCS_PROGRAM_SVC_URL=http://program:8004
```

### Microservices Override (`docker-compose.microservices.yml`)

An alternative compose file that defines the same 4 app services with a simpler configuration:
- No `env_file` directives (inline env only).
- No `image` tag (just `build: ./backend`).
- Simple `depends_on` lists (no health conditions).
- Gateway additionally depends on `ollama` and sets `OLLAMA_HOST: http://ollama:11434`.

Usage: `docker compose -f docker-compose.yml -f docker-compose.microservices.yml up -d`

### Backend Dockerfile (`backend/Dockerfile`)

```dockerfile
FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends git && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "gateway.py"]
```

- **Base image:** `python:3.11-slim`
- **System deps:** `git` (required for `gitpython` clone operations)
- **Default CMD:** `python gateway.py` (overridden per-service in compose)
- No `EXPOSE` directive — ports are configured via compose.

**`.dockerignore`** excludes: `__pycache__`, `*.pyc`, `.env*`, `ingested_codebases`, `*.egg-info`, `.venv`, `venv`.

---

## CI/CD Pipeline (`.github/workflows/ci.yml`)

### Trigger Conditions

- **Push** to `main` or `TTS` branches
- **Pull request** targeting `main`

### Jobs

```
┌─────────────┐  ┌────────────────┐  ┌────────────────┐  ┌──────────────┐
│  frontend   │  │  backend-lint  │  │  backend-test  │  │ docker-build │
│             │  │                │  │                │  │              │
│ Node 20     │  │ Python 3.11   │  │ Python 3.11   │  │ Docker       │
│ npm ci      │  │ ruff check    │  │ pip install    │  │ build only   │
│ tsc --noEmit│  │ E/F/W rules   │  │ pytest -v     │  │ (no push)    │
│ vite build  │  │               │  │               │  │ GHA cache    │
└─────────────┘  └────────────────┘  └────────────────┘  └──────────────┘
```

#### Job: `frontend`

```yaml
- uses: actions/setup-node@v4 (Node 20, npm cache)
- npm ci
- npx tsc --noEmit          # TypeScript type checking
- npm run build              # Vite production build
  env:
    VITE_CLERK_PUBLISHABLE_KEY: ${{ secrets.VITE_CLERK_PUBLISHABLE_KEY }}
```

#### Job: `backend-lint`

```yaml
- uses: actions/setup-python@v5 (Python 3.11)
- pip install ruff
- ruff check backend/ --select E,F,W --ignore E501,F401,F811,W503
```

Ignores: `E501` (line too long), `F401` (unused imports), `F811` (redefined unused), `W503` (line break before binary operator).

#### Job: `backend-test`

```yaml
- uses: actions/setup-python@v5 (Python 3.11, pip cache)
- apt-get install -y git
- pip install -r requirements.txt pytest
- pytest tests/ -v
  env:
    DATABASE_URL: ""           # Stub — no real DB in CI
    MONGODB_URI: ""
    MILVUS_URI: ""
    CLERK_JWKS_URL: ""
```

#### Job: `docker-build`

```yaml
- uses: docker/setup-buildx-action@v3
- uses: docker/build-push-action@v5
  with:
    context: ./backend
    push: false               # Build only, no registry push
    tags: xplore-backend:ci
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

---

## Deployment Options

### Option 1: Local Development

```bash
# Start infrastructure only
docker compose up postgres mongo milvus ollama -d

# Start backend (monolith mode)
cd backend && python main.py

# Start frontend
cd frontend && npm run dev
```

The monolith runs on port 8000 with hot-reload enabled by default. Frontend dev server runs on port 5173 (auto-increments if busy).

### Option 2: Docker Compose (Full Microservices)

```bash
docker compose up -d --build
```

All 8 services start. The gateway on port 8000 proxies to graph/rag/program services. Frontend is handled separately (Vite dev server or static hosting).

### Option 3: Vercel (Serverless)

Each microservice deploys as a Vercel serverless function:

```
backend/api/gateway.py  → Catch-all routes, AI/meta
backend/api/graph.py    → /analyze, /files, /graph, /analyses, /jobs
backend/api/program.py  → /program, /generate, /generated
backend/api/rag.py      → /rag
```

Configuration in `backend/vercel.json` with route rewrites. Max function durations: 300s for graph/program, 60s for rag/gateway.

**Limitation:** WebSockets are not supported on Vercel. Narrator and streaming chat features are unavailable in this mode.

Frontend deploys as a standard Vite SPA with `frontend/vercel.json`:
- Build: `npm run build` → `dist/`
- SPA rewrite: `/(.*) → /index.html`
- Asset caching: 1-year immutable for `/assets/`

### Option 4: Railway (PaaS)

Backend deploys as a monolith via `backend/railway.json`:
- Uses the backend Dockerfile.
- Start command: `XPLORE_RELOAD=false python main.py`
- Healthcheck: `/health` with 300s timeout.
- Restart policy: `ON_FAILURE`, max 5 retries.

---

## Environment Configuration

All configuration is loaded by `shared/config.py` from environment variables.

### Database Variables

| Variable                         | Description                        | Default                                    |
|----------------------------------|------------------------------------|--------------------------------------------|
| `DATABASE_URL`                   | PostgreSQL connection string       | `postgresql://postgres:postgres@localhost:5432/ezdocs` |
| `MONGODB_URI`                    | MongoDB connection string          | `mongodb://localhost:27017`                |
| `MONGODB_DB`                     | MongoDB database name              | `ezdocs`                                   |
| `MONGODB_GENERATED_COLLECTION`   | MongoDB collection for code gen    | `generated_code`                           |
| `MILVUS_URI`                     | Milvus gRPC endpoint              | `http://localhost:19530`                   |
| `MILVUS_COLLECTION`              | Milvus collection name            | `ezdocs_graph_embeddings`                  |
| `EZDOCS_EMBEDDING_DIM`           | Embedding vector dimension        | `384`                                      |

### LLM Variables

| Variable                    | Description                        | Default                         |
|-----------------------------|------------------------------------|---------------------------------|
| `OLLAMA_HOST`               | Ollama server URL (empty=disabled) | (empty)                         |
| `EZDOCS_MODEL`              | Ollama model name                  | `qwen2.5-coder:3b`             |
| `HUGGINGFACE_HUB_TOKEN`    | HuggingFace API token              | (optional)                      |
| `EZDOCS_HF_MODEL`          | HuggingFace model ID               | `Qwen/Qwen3-235B-A22B`         |
| `EZDOCS_HF_EMBEDDING_MODEL`| HuggingFace embedding model        | `nomic-ai/nomic-embed-text-v1.5`|
| `OPENAI_API_KEY`            | OpenAI API key (server-side)       | (optional)                      |
| `ANTHROPIC_API_KEY`         | Anthropic API key (server-side)    | (optional)                      |

### Server Variables

| Variable                    | Description                        | Default                |
|-----------------------------|------------------------------------|------------------------|
| `EZDOCS_HOST`               | Bind host                          | `0.0.0.0`             |
| `EZDOCS_PORT`               | Bind port                          | `8000`                 |
| `EZDOCS_RELOAD`             | Hot-reload enabled                 | `true`                 |
| `EZDOCS_WS_MAX_SIZE`        | WebSocket max message size         | `10000000` (10MB)      |
| `EZDOCS_MAX_FILES`          | Default max files per analysis     | `200`                  |
| `EZDOCS_MAX_FILES_CEILING`  | Absolute max files limit           | `1000`                 |
| `EZDOCS_MAX_FILE_SIZE`      | Skip files larger than (bytes)     | `1048576` (1MB)        |
| `EZDOCS_PARSE_WORKERS`      | Parallel file parsing threads      | `1`                    |

### Auth Variables

| Variable              | Description                        | Default    |
|-----------------------|------------------------------------|------------|
| `CLERK_JWKS_URL`      | Clerk JWKS endpoint for JWT verify | (optional) |

### Microservice URLs

| Variable                  | Description                    | Default                         |
|---------------------------|--------------------------------|---------------------------------|
| `EZDOCS_GRAPH_SVC_URL`   | Graph microservice URL         | `http://localhost:8001`         |
| `EZDOCS_AI_SVC_URL`      | AI microservice URL            | `http://localhost:8002`         |
| `EZDOCS_RAG_SVC_URL`     | RAG microservice URL           | `http://localhost:8003`         |
| `EZDOCS_PROGRAM_SVC_URL` | Program microservice URL       | `http://localhost:8004`         |

### Frontend Variables

| Variable                       | Description                   |
|--------------------------------|-------------------------------|
| `VITE_CLERK_PUBLISHABLE_KEY`  | Clerk publishable key         |
| `VITE_API_URL`                | Backend API base URL          |

---

## Network Topology

### Local Development

```
Browser (:5173)
    │
    ├── REST/WS → Backend monolith (:8000)
    │                 │
    │                 ├── asyncpg → Postgres (:7998)
    │                 ├── pymilvus → Milvus (:19530)
    │                 ├── pymongo → MongoDB (:27017)
    │                 └── openai SDK → Ollama (:11434)
    │
    └── Clerk API → Clerk (cloud auth)
```

### Docker Microservices

```
Browser
    │
    ├── REST/WS → Gateway (:8000)
    │                 ├── AI/Narrator (in-process)
    │                 ├── httpx proxy → Graph (:8001)
    │                 ├── httpx proxy → RAG (:8003)
    │                 └── httpx proxy → Program (:8004)
    │
    └── All services share Docker network:
          ├── postgres (container: ezdocs-postgres)
          ├── milvus (container: ezdocs-milvus)
          ├── mongo (container: ezdocs-mongo)
          └── ollama (container: ezdocs-ollama)
```

### Port Map

| Service          | Container Port | Host Port | Notes                    |
|------------------|----------------|-----------|--------------------------|
| PostgreSQL       | 5432           | 7998      | Non-standard host port   |
| MongoDB          | 27017          | 27017     |                          |
| Milvus (gRPC)    | 19530          | 19530     |                          |
| Milvus (metrics) | 9091           | 9091      |                          |
| Ollama           | 11434          | 11434     |                          |
| Gateway          | 8000           | 8000      |                          |
| Graph Service    | 8001           | 8001      |                          |
| AI Service       | 8002           | —         | Not deployed separately  |
| RAG Service      | 8003           | 8003      |                          |
| Program Service  | 8004           | 8004      |                          |
| Frontend (dev)   | 5173           | 5173      | Auto-increments if busy  |
