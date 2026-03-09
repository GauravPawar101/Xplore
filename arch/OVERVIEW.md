# EzDocs — System Architecture Overview

## What Is EzDocs?

EzDocs is a full-stack application that turns codebases into **interactive dependency graphs** with AI-powered narration, conversational RAG-based code search, and program-to-code generation. Users point it at a GitHub repo, local folder, or ZIP archive, and the system:

1. Parses every source file with **tree-sitter** grammars.
2. Builds a directed **dependency graph** (functions -> calls, classes -> instantiations).
3. Runs a **reconciliation engine** that classifies files into three layers: root entry files, their direct local dependencies, and everything else.
4. Renders the graph as a navigable **React Flow** canvas with progressive node expansion.
5. Provides an **AI narrator** that walks through the codebase like a guided tour (with browser TTS).
6. Enables **code search** via keyword retrieval (Postgres) and optional vector search (Milvus).
7. Lets users sketch **program intent graphs** and generate code from them.

---

## High-Level Architecture Diagram

```
+-------------------------------------------------------------+
|                        FRONTEND                              |
|  React 18 + TypeScript + Vite + React Flow + Clerk Auth     |
|  +----------+  +-----------+  +---------+  +------------+   |
|  | CodeMap  |  | AppLayout |  | Landing |  | LibraryNode|   |
|  | (IDE)    |  | (Shell)   |  |  Page   |  | (custom)   |   |
|  +----+-----+  +-----------+  +---------+  +------------+   |
|       |  REST + WebSocket                                    |
+-------+------------------------------------------------------+
        |
        v
+-------------------------------------------------------------+
|                    BACKEND (FastAPI + Python)                 |
|                                                              |
|  Monolith (main.py :8000) -- all routers in one process     |
|                        OR                                    |
|  Microservices:                                              |
|  +----------+  +----------+  +--------+  +--------------+   |
|  | Gateway  |  |  Graph   |  |  RAG   |  |   Program    |   |
|  | :8000    |  |  :8001   |  | :8003  |  |   :8004      |   |
|  | AI,Narr. |  | Parse,   |  | Index, |  | Intent graph |   |
|  | WebSocket|  | Analyze  |  | Query  |  | Code gen     |   |
|  +----+-----+  +----+-----+  +---+----+  +------+-------+   |
|       |             |            |               |           |
|       +-------------+------------+---------------+           |
|                          |                                   |
|              +-----------+-----------+                       |
|              |     Shared Layer      |                       |
|              |  config, db, ai,      |                       |
|              |  parser, schemas,     |                       |
|              |  embedding, crawler   |                       |
|              +-----------+-----------+                       |
+----------------------------+---------------------------------+
                             |
          +------------------+------------------+
          v                  v                  v
  +--------------+  +--------------+  +--------------+
  |  PostgreSQL  |  |    Milvus    |  |   MongoDB    |
  |  Analyses,   |  |   Vector     |  |  Generated   |
  |  Graphs,     |  |   Embeddings |  |  Code Blobs  |
  |  Programs    |  |   (RAG ANN)  |  |              |
  +--------------+  +--------------+  +--------------+
```

---

## Tech Stack

| Layer           | Technology                                                     |
|-----------------|----------------------------------------------------------------|
| Frontend        | React 18, TypeScript, Vite, React Flow, Clerk (auth), Framer Motion |
| Backend         | Python 3.11, FastAPI, Uvicorn, asyncpg, httpx                 |
| Code Parsing    | tree-sitter (Python, JS/TS, Java, Rust, C/C++, Go)           |
| Graph Engine    | NetworkX -> React Flow serialization                           |
| AI / LLM       | Ollama (local, primary), HuggingFace Inference (cloud fallback) |
| LLM Providers   | OpenAI, Anthropic, HuggingFace Inference (for code generation) |
| Vector DB       | Milvus 2.4 (IVF_FLAT, inner product)                         |
| Relational DB   | PostgreSQL 16                                                 |
| Document DB     | MongoDB 7                                                     |
| Auth            | Clerk (JWT via JWKS verification in backend)                  |
| Infrastructure  | Docker Compose, Vercel (serverless), Railway                  |
| CI/CD           | GitHub Actions (lint, test, type-check, Docker build)         |

---

## Deployment Modes

### 1. Monolith (Development)

A single FastAPI process (`backend/main.py` on port 8000) mounts all routers. Infrastructure services (Postgres, Milvus, MongoDB) run in Docker or locally. Ollama runs as a separate local process or Docker container.

### 2. Microservices (Docker Compose)

Four separate FastAPI processes:
- **Gateway** (:8000) — AI, narrator WebSocket, meta; proxies to other services via httpx
- **Graph** (:8001) — Analysis, file explorer, graph persistence
- **RAG** (:8003) — Vector indexing and keyword+vector retrieval
- **Program** (:8004) — Intent graphs, code generation

The main `docker-compose.yml` defines all 8 services (4 infra + 4 app). `docker-compose.microservices.yml` provides an overlay alternative.

### 3. Serverless (Vercel)

Each microservice has a `backend/api/*.py` entry point that re-exports the FastAPI app for Vercel's Python runtime. Configuration in `backend/vercel.json`. Note: WebSockets are not supported on Vercel.

### 4. Railway (PaaS)

Backend deploys as a monolith via `backend/railway.json`, running `python main.py` with a `/health` healthcheck.

---

## Core Data Flow

```
User Input (GitHub URL / local path / ZIP)
        |
        v
   +---------+     tree-sitter      +--------------+
   | Ingest  | ------------------->  | GraphBuilder  |
   | clone / |     parse files       | NetworkX     |
   | extract |                       | graph        |
   +---------+                       +------+-------+
                                            |
                          +-----------------+-----------------+
                          v                 v                 v
                   +------------+   +------------+   +------------+
                   | React Flow |   |  Postgres  |   |   Milvus   |
                   | JSON       |   |  persist   |   |  embeddings|
                   | (frontend) |   |  (nodes/   |   |  (optional)|
                   |            |   |   edges)   |   |            |
                   +------------+   +------------+   +------------+
```

---

## Repository Structure

```
EzDocs/
├── docker-compose.yml                        # All services (infra + app microservices)
├── docker-compose.microservices.yml          # Microservices-only overlay
├── GETTING_STARTED.md
├── README.md
│
├── .github/
│   └── workflows/
│       └── ci.yml                            # CI: lint, test, type-check, Docker build
│
├── arch/                                     # Architecture documentation (this folder)
│   ├── AI_PIPELINE.md
│   ├── API_REFERENCE.md
│   ├── BACKEND.md
│   ├── DATA_LAYER.md
│   ├── FRONTEND.md
│   ├── INFRASTRUCTURE.md
│   └── OVERVIEW.md                           # This file
│
├── backend/
│   ├── Dockerfile                            # Shared image for all backend services
│   ├── gateway.py                            # Microservice gateway entry point
│   ├── main.py                               # Monolith entry point (all routers)
│   ├── railway.json                          # Railway PaaS deployment config
│   ├── requirements.txt                      # Python dependencies
│   ├── vercel.json                           # Vercel serverless config
│   │
│   ├── api/                                  # Vercel serverless entry points
│   │   ├── gateway.py
│   │   ├── graph.py
│   │   ├── program.py
│   │   └── rag.py
│   │
│   ├── gateway/                              # Gateway FastAPI app (proxy + AI/narrator)
│   │   ├── __init__.py
│   │   └── app.py
│   │
│   ├── graph/                                # Graph analysis engine
│   │   ├── __init__.py
│   │   ├── builder.py                        # Core graph construction (GraphBuilder)
│   │   └── reconciliation.py                 # 3-layer file classification engine
│   │
│   ├── jobs/                                 # Background job processing
│   │   ├── __init__.py
│   │   ├── handlers.py                       # Job workers (graph_analyze, graph_explain)
│   │   └── router.py                         # Job queue HTTP API (used by main.py)
│   │
│   ├── program/                              # Program microservice
│   │   ├── __init__.py
│   │   └── app.py
│   │
│   ├── rag/                                  # RAG microservice
│   │   ├── __init__.py
│   │   └── app.py
│   │
│   ├── routers/                              # FastAPI route handlers (shared by all modes)
│   │   ├── __init__.py
│   │   ├── ai.py                             # /explain, ws:/ws/explain, ws:/ws/chat
│   │   ├── graph.py                          # /analyze, /files, /graph, /analyses
│   │   ├── jobs.py                           # /jobs/* (duplicate of jobs/router.py)
│   │   ├── meta.py                           # /health
│   │   ├── narrator_ws.py                    # ws:/ws/narrate, ws:/ws/narrate/node
│   │   ├── program.py                        # /program, /generate/code, /generated
│   │   └── rag.py                            # /rag/query, /rag/index
│   │
│   ├── services/                             # Standalone microservice FastAPI apps
│   │   ├── __init__.py
│   │   ├── ai_svc.py                         # AI service (:8002)
│   │   ├── graph_svc.py                      # Graph service (:8001)
│   │   ├── program_svc.py                    # Program service (:8004)
│   │   └── rag_svc.py                        # RAG service (:8003)
│   │
│   ├── shared/                               # Shared utilities and infrastructure
│   │   ├── __init__.py
│   │   ├── ai.py                             # LLM interaction (Ollama / HF streaming)
│   │   ├── auth.py                           # Clerk JWT verification via JWKS
│   │   ├── config.py                         # Environment config loader
│   │   ├── crawler.py                        # GitHub API file crawler
│   │   ├── db.py                             # PostgreSQL (asyncpg) connection + queries
│   │   ├── embedding.py                      # HF Inference embedding generation
│   │   ├── ingest.py                         # Clone / ZIP extraction
│   │   ├── jobqueue.py                       # In-memory job queue
│   │   ├── llm_providers.py                  # Multi-provider LLM (OpenAI/Anthropic/HF)
│   │   ├── milvus_service.py                 # Milvus vector DB client
│   │   ├── mongo_service.py                  # MongoDB client
│   │   ├── narrator.py                       # Linear BFS narrator (used by narrator_ws)
│   │   ├── narrator_graph.py                 # LangGraph interactive narrator (unused)
│   │   ├── parser.py                         # tree-sitter multi-language parser
│   │   ├── rag_chain.py                      # LangChain RAG chain builder (unused)
│   │   ├── schemas.py                        # Pydantic request/response models
│   │   ├── state.py                          # In-memory shared state (graph_cache)
│   │   └── migrations/
│   │       ├── 001_init.sql
│   │       ├── 002_entry_score.sql
│   │       ├── 002_init.sql
│   │       └── 003_chat.sql
│   │
│   └── tests/
│       └── test_entrypoint.py
│
├── docs/                                     # Project planning and research docs
│   ├── ARCHITECTURE.md
│   ├── DEPLOY_AND_OPTIMIZE_PLAN.md
│   ├── GRAPH_DB_AND_RAG_FEASIBILITY.md
│   ├── MICROSERVICES.md
│   ├── NEO4J_RAG_AND_CODE_GEN.md
│   ├── OPTION_C_FREE_TIER.md
│   └── PRODUCT_AND_DATA_ARCHITECTURE.md
│
└── frontend/
    ├── index.html
    ├── package.json
    ├── postcss.config.js
    ├── tailwind.config.js
    ├── tsconfig.json
    ├── tsconfig.node.json
    ├── vercel.json
    ├── vite.config.ts
    └── src/
        ├── App.tsx                           # Unused wrapper (dead code)
        ├── CodeMap.css                       # IDE styling (dark theme)
        ├── CodeMap.tsx                       # Main IDE component (~1115 lines)
        ├── index.css                         # Tailwind imports
        ├── main.tsx                          # React entry point + routing
        ├── components/
        │   ├── AuthRequestInterceptor.tsx    # Clerk auth token injection (axios)
        │   ├── context.ts                    # FocusCtx + MemberClickCtx
        │   ├── EzEdge.tsx                    # Custom React Flow edge renderer
        │   ├── EzNode.tsx                    # Custom React Flow node renderer
        │   ├── FileGroupNode.tsx             # Architect-view file group node
        │   ├── FileItem.tsx                  # File explorer sidebar item
        │   └── LibraryNode.tsx               # 3rd-party dependency node (not yet wired)
        ├── config/
        │   └── constants.ts                  # API_BASE, WS_BASE, layout constants
        ├── context/
        │   └── TourContext.tsx               # Narrator tour state provider
        ├── lib/
        │   └── layoutUtils.ts               # Graph layout algorithms (tree + architect)
        ├── pages/
        │   ├── AppLayout.tsx                 # App shell with header nav
        │   ├── ConversationPage.tsx          # WebSocket chat UI
        │   ├── LandingPage.tsx               # Auth landing page
        │   ├── MyGraphsPage.tsx              # Saved program graphs browser
        │   └── ProtectedRoute.tsx            # Clerk auth guard
        └── types/
            └── index.ts                      # Shared TypeScript type definitions
```

---

## Documentation Index

| Document                                | Description                                      |
|-----------------------------------------|--------------------------------------------------|
| [OVERVIEW.md](OVERVIEW.md)             | This file -- system overview                     |
| [BACKEND.md](BACKEND.md)               | Backend architecture, routers, services, shared  |
| [FRONTEND.md](FRONTEND.md)             | Frontend components, routing, layout             |
| [DATA_LAYER.md](DATA_LAYER.md)         | Databases, schemas, migrations, data flow        |
| [AI_PIPELINE.md](AI_PIPELINE.md)       | AI/LLM, narrator, RAG, code generation           |
| [INFRASTRUCTURE.md](INFRASTRUCTURE.md) | Docker, CI/CD, deployment, environment config    |
| [API_REFERENCE.md](API_REFERENCE.md)   | Complete HTTP + WebSocket API reference           |
