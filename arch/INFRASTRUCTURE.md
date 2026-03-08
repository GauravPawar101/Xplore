# Infrastructure — Docker, CI/CD, Deployment

## Docker Setup

### Infrastructure Services (`docker-compose.yml`)

The base compose file runs the four infrastructure dependencies:

```yaml
services:
  postgres:     # PostgreSQL 16 Alpine
  mongo:        # MongoDB 7
  milvus:       # Milvus 2.4.0 standalone
  ollama:       # Ollama (latest) with GPU passthrough
```

| Service    | Image                                    | Port(s)       | Volume                 | Healthcheck               |
|------------|------------------------------------------|---------------|------------------------|---------------------------|
| postgres   | `postgres:16-alpine`                    | 7998 → 5432  | `pg_data`              | `pg_isready`              |
| mongo      | `mongo:7`                               | 27017 → 27017| `mongo_data`           | `mongosh --eval db.admin` |
| milvus     | `milvusdb/milvus:v2.4.0`               | 19530, 9091  | `milvus_data`          | `curl /healthz`           |
| ollama     | `ollama/ollama:latest`                  | 11434 → 11434| `ollama_data`          | `curl /api/tags`          |

**Ollama auto-setup:** The Ollama container runs a custom entrypoint that:
1. Starts `ollama serve` in the background.
2. Waits for the server to be ready.
3. Pulls `qwen2.5-coder:8b` and `nomic-embed-text` models.
4. Keeps the server running as PID 1.

**Milvus standalone mode:** Runs with embedded etcd (`ETCD_USE_EMBED=true`) and local storage, avoiding the need for separate etcd/MinIO containers.

### Full Microservices Stack (`docker-compose.microservices.yml`)

Extends the base compose with four backend services:

```yaml
services:
  # Infrastructure (inherited from docker-compose.yml)
  postgres:
  mongo:
  milvus:
  ollama:

  # Application services
  gateway:     # Port 8000
  graph:       # Port 8001
  rag:         # Port 8003
  program:     # Port 8004
```

| Service  | Port | Command                        | Dependencies                     |
|----------|------|--------------------------------|----------------------------------|
| gateway  | 8000 | `python gateway.py`            | postgres, ollama                |
| graph    | 8001 | `uvicorn graph.app:app`        | postgres, ollama                |
| rag      | 8003 | `uvicorn rag.app:app`          | postgres, milvus, ollama        |
| program  | 8004 | `uvicorn program.app:app`      | postgres, mongo, ollama         |

All application services share the same Docker image built from `backend/Dockerfile`.

**Inter-service communication** via environment variables:
```
XPLORE_GRAPH_SVC_URL=http://graph:8001
XPLORE_RAG_SVC_URL=http://rag:8003
XPLORE_PROGRAM_SVC_URL=http://program:8004
```

### Backend Dockerfile (`backend/Dockerfile`)

Multi-purpose image for all backend services:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
```

The CMD is overridden per-service in the microservices compose file. The `.dockerignore` file excludes `tests/`, `__pycache__/`, `.env`, and dev artifacts.

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
- uses: actions/setup-node@v4 (Node 20)
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
- ruff check --select E,F,W --ignore E501,F401,F811,W503 .
```

Ignores:
- `E501` — Line too long
- `F401` — Unused imports
- `F811` — Redefined unused names
- `W503` — Line break before binary operator

#### Job: `backend-test`

```yaml
- uses: actions/setup-python@v5 (Python 3.11)
- pip install -r requirements.txt
- pytest tests/ -v
  env:
    DATABASE_URL: ""           # Stub — no real DB in CI
    OLLAMA_BASE_URL: ""
    HF_TOKEN: ""
```

#### Job: `docker-build`

```yaml
- uses: docker/setup-buildx-action@v3
- uses: docker/build-push-action@v5
  with:
    context: ./backend
    push: false               # Build only, no registry push
    cache-from: type=gha
    cache-to: type=gha,mode=max
```

---

## Deployment Options

### Option 1: Local Development

```bash
# Start infrastructure
docker compose up -d

# Start backend (monolith)
cd backend && uvicorn main:app --reload --port 8000

# Start frontend
cd frontend && npm run dev
```

Or use `start.bat` on Windows which automates Docker Compose and environment setup.

### Option 2: Docker Compose (Full Stack)

```bash
docker compose -f docker-compose.yml -f docker-compose.microservices.yml up -d --build
```

Services available at:
- Frontend: handled separately (Vite dev server or static hosting)
- Gateway: `http://localhost:8000`
- Graph: `http://localhost:8001`
- RAG: `http://localhost:8003`
- Program: `http://localhost:8004`

### Option 3: Vercel (Serverless)

Each microservice deploys as a Vercel serverless function:

```
backend/api/gateway.py  → Gateway routes
backend/api/graph.py    → Graph routes
backend/api/program.py  → Program routes
backend/api/rag.py      → RAG routes
```

Configuration in `backend/vercel.json` rewrites all routes to the appropriate handler.

Frontend deploys as a standard Vite static build with `frontend/vercel.json`.

### Option 4: Railway

Backend can deploy to Railway using `backend/railway.json` which specifies the build and start commands.

---

## Environment Configuration

All configuration is loaded by `shared/config.py` from environment variables.

### Required Variables

| Variable              | Description                        | Default              |
|-----------------------|------------------------------------|----------------------|
| `DATABASE_URL`        | PostgreSQL connection string       | (required)           |
| `OLLAMA_BASE_URL`     | Ollama server URL                  | `http://localhost:11434` |

### Optional Variables

| Variable                  | Description                        | Default                     |
|---------------------------|------------------------------------|-----------------------------|
| `MONGODB_URI`             | MongoDB connection string          | `mongodb://localhost:27017` |
| `MILVUS_URI`              | Milvus gRPC endpoint              | `http://localhost:19530`    |
| `MILVUS_COLLECTION`       | Milvus collection name            | `xplore_embeddings`         |
| `HF_TOKEN`                | HuggingFace API token(s)          | (optional)                  |
| `HF_MODEL_ID`             | HuggingFace model ID              | (configurable)              |
| `OPENAI_API_KEY`          | OpenAI API key                    | (optional)                  |
| `ANTHROPIC_API_KEY`       | Anthropic API key                 | (optional)                  |
| `CLERK_SECRET_KEY`        | Clerk backend secret              | (optional)                  |
| `CLERK_PUBLISHABLE_KEY`   | Clerk frontend publishable key    | (optional)                  |
| `UPSTASH_REDIS_URL`       | Upstash Redis REST URL            | (optional)                  |
| `UPSTASH_REDIS_TOKEN`     | Upstash Redis REST token          | (optional)                  |
| `XPLORE_GRAPH_SVC_URL`    | Graph microservice URL            | (microservices only)        |
| `XPLORE_RAG_SVC_URL`      | RAG microservice URL              | (microservices only)        |
| `XPLORE_PROGRAM_SVC_URL`  | Program microservice URL          | (microservices only)        |
| `XPLORE_MAX_CHAT_SESSIONS`| Max concurrent chat sessions      | `100`                       |
| `EMBEDDING_DIM`           | Embedding vector dimension        | `768`                       |
| `GITHUB_TOKEN`            | GitHub API token for crawling     | (optional)                  |

### Frontend Variables

| Variable                        | Description                   |
|---------------------------------|-------------------------------|
| `VITE_CLERK_PUBLISHABLE_KEY`   | Clerk publishable key         |
| `VITE_API_BASE_URL`            | Backend API base URL          |

---

## `start.bat` — Windows Setup Script

Automated setup for Windows development:

```
1. Check Docker is installed and running
2. Detect docker compose (plugin) vs docker-compose (standalone)
3. Check for Git (soft warning if missing)
4. Create backend/.env from .env.example if missing
5. Prompt user to fill in credentials
6. Remind about frontend/.env.local
7. Run: docker compose up -d --build
8. Print all service endpoint URLs
```

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
    │                 ├── httpx → Ollama (:11434)
    │                 └── upstash → Upstash Redis (cloud)
    │
    └── Clerk API → Clerk (cloud auth)
```

### Docker Microservices

```
Browser
    │
    ├── REST/WS → Gateway (:8000)
    │                 ├── HTTP → Graph (:8001)
    │                 ├── HTTP → RAG (:8003)
    │                 └── HTTP → Program (:8004)
    │
    └── All services share:
          ├── postgres (Docker network)
          ├── milvus (Docker network)
          ├── mongo (Docker network)
          └── ollama (Docker network)
```
