# Xplore (EzDocs) — System Architecture Overview

## What Is Xplore?

Xplore is a full-stack application that turns codebases into **interactive dependency graphs** with AI-powered narration, conversational RAG-based code search, and program-to-code generation. Users point it at a GitHub repo, local folder, or ZIP archive, and the system:

1. Parses every source file with **tree-sitter** grammars.
2. Builds a directed **dependency graph** (functions → calls, classes → instantiations).
3. Renders the graph as a navigable **React Flow** canvas.
4. Provides an **AI narrator** that walks through the codebase like a guided tour.
5. Enables **semantic code search** via hybrid keyword + vector retrieval (RAG).
6. Lets users sketch **program intent graphs** and generate code from them.

---

## High-Level Architecture Diagram

```
┌─────────────────────────────────────────────────────────────┐
│                        FRONTEND                              │
│  React + TypeScript + React Flow + Clerk Auth                │
│  ┌──────────┐  ┌───────────┐  ┌─────────┐  ┌────────────┐  │
│  │ CodeMap  │  │ AppLayout │  │ Landing │  │ LibraryNode│  │
│  │ (IDE)    │  │ (Shell)   │  │  Page   │  │ (custom)   │  │
│  └────┬─────┘  └───────────┘  └─────────┘  └────────────┘  │
│       │  REST + WebSocket                                    │
└───────┼──────────────────────────────────────────────────────┘
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│                    BACKEND (FastAPI + Python)                 │
│                                                              │
│  ┌──────────┐  ┌──────────┐  ┌────────┐  ┌──────────────┐  │
│  │ Gateway  │  │  Graph   │  │  RAG   │  │   Program    │  │
│  │ :8000    │  │  :8001   │  │ :8003  │  │   :8004      │  │
│  │ AI,Narr. │  │ Parse,   │  │ Index, │  │ Intent graph │  │
│  │ WebSocket│  │ Analyze  │  │ Query  │  │ Code gen     │  │
│  └────┬─────┘  └────┬─────┘  └───┬────┘  └──────┬───────┘  │
│       │             │            │               │           │
│       └─────────────┴────────────┴───────────────┘           │
│                          │                                    │
│              ┌───────────┴───────────┐                       │
│              │     Shared Layer      │                       │
│              │  config, db, ai,      │                       │
│              │  parser, schemas,     │                       │
│              │  embedding, crawler   │                       │
│              └───────────┬───────────┘                       │
└──────────────────────────┼───────────────────────────────────┘
                           │
        ┌──────────────────┼──────────────────┐
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  PostgreSQL  │  │    Milvus    │  │   MongoDB    │
│  Analyses,   │  │   Vector     │  │  Generated   │
│  Graphs,     │  │   Embeddings │  │  Code Blobs  │
│  Chat, Users │  │   (RAG ANN)  │  │              │
└──────────────┘  └──────────────┘  └──────────────┘
        │
        ▼
┌──────────────┐
│    Ollama    │
│   LLM Local  │
└──────────────┘
```

---

## Tech Stack

| Layer           | Technology                                                     |
|-----------------|----------------------------------------------------------------|
| Frontend        | React 18, TypeScript, Vite, React Flow, Clerk (auth)          |
| Backend         | Python 3.11, FastAPI, Uvicorn, asyncpg                        |
| Code Parsing    | tree-sitter (Python, JS/TS, Java, Rust, C/C++, Go)           |
| Graph Engine    | NetworkX → React Flow serialization                           |
| AI / LLM       | Ollama (local), LangChain, LangGraph                          |
| LLM Providers   | Ollama (default), OpenAI, Anthropic, HuggingFace Inference   |
| Vector DB       | Milvus 2.4 (IVF_FLAT, inner product)                         |
| Relational DB   | PostgreSQL 16 with pg_trgm extension                         |
| Document DB     | MongoDB 7                                                     |
| Auth            | Clerk (JWT verification in backend)                           |
| Infrastructure  | Docker Compose, Vercel (serverless), Railway                  |
| CI/CD           | GitHub Actions (lint, test, type-check, Docker build)         |

---

## Deployment Modes

### 1. Monolith (Development)

A single FastAPI process (`backend/main.py` on port 8000) mounts all routers. Infrastructure services (Postgres, Milvus, MongoDB, Ollama) run in Docker.

### 2. Microservices (Production Docker)

Four separate FastAPI processes behind a gateway:
- **Gateway** (:8000) — AI, narrator WebSocket, meta
- **Graph** (:8001) — Analysis, file explorer, graph persistence
- **RAG** (:8003) — Vector indexing and hybrid retrieval
- **Program** (:8004) — Intent graphs, code generation

Orchestrated via `docker-compose.microservices.yml`.

### 3. Serverless (Vercel)

Each microservice has a `backend/api/*.py` entry point that re-exports the FastAPI app for Vercel's Python runtime. Configuration in `backend/vercel.json`.

---

## Core Data Flow

```
User Input (GitHub URL / local path / ZIP)
        │
        ▼
   ┌─────────┐     tree-sitter      ┌────────────┐
   │ Ingest  │ ──────────────────▶  │ GraphBuilder │
   │ clone / │     parse files       │ NetworkX    │
   │ extract │                       │ graph       │
   └─────────┘                       └──────┬─────┘
                                            │
                          ┌─────────────────┼─────────────────┐
                          ▼                 ▼                 ▼
                   ┌────────────┐   ┌────────────┐   ┌────────────┐
                   │ React Flow │   │  Postgres  │   │   Milvus   │
                   │ JSON       │   │  persist   │   │  embeddings│
                   │ (frontend) │   │  (nodes/   │   │  (RAG)     │
                   │            │   │   edges)   │   │            │
                   └────────────┘   └────────────┘   └────────────┘
```

---

## Repository Structure

```
EzDocs/
├── .env.example                              # Environment variable template
├── .gitignore
├── docker-compose.microservices.yml          # Full microservices stack in Docker
├── docker-compose.yml                        # Infrastructure services (Postgres, Milvus, etc.)
├── GETTING_STARTED.md                        # Setup and quickstart guide
├── README.md
│
├── .github/
│   └── workflows/
│       └── ci.yml                            # CI pipeline (lint, test, type-check, Docker build)
│
├── arch/                                     # Architecture documentation (this folder)
│   ├── AI_PIPELINE.md                        # LLM systems: narrator, RAG, code generation
│   ├── API_REFERENCE.md                      # Complete HTTP + WebSocket API reference
│   ├── BACKEND.md                            # Backend entry points, routers, services
│   ├── DATA_LAYER.md                         # PostgreSQL, Milvus, MongoDB schemas
│   ├── FRONTEND.md                           # Frontend components, routing, layout
│   ├── INFRASTRUCTURE.md                     # Docker, CI/CD, deployment, env vars
│   └── OVERVIEW.md                           # This file — system overview
│
├── backend/
│   ├── Dockerfile                            # Backend Docker image
│   ├── gateway.py                            # Microservice gateway entry point
│   ├── main.py                               # Monolith entry point (all routers)
│   ├── railway.json                          # Railway deployment config
│   ├── requirements.txt                      # Python dependencies
│   ├── vercel.json                           # Vercel serverless config
│   │
│   ├── api/                                  # Vercel serverless entry points
│   │   ├── gateway.py
│   │   ├── graph.py
│   │   ├── program.py
│   │   └── rag.py
│   │
│   ├── gateway/                              # Gateway FastAPI app
│   │   ├── __init__.py
│   │   └── app.py                            # Gateway app (AI, narrator, meta)
│   │
│   ├── graph/                                # Graph microservice
│   │   ├── __init__.py
│   │   ├── app.py                            # FastAPI app (:8001)
│   │   └── builder.py                        # Core graph construction engine
│   │
│   ├── jobs/                                 # Background job processing
│   │   ├── __init__.py
│   │   ├── handlers.py                       # Background job workers
│   │   └── router.py                         # Job queue HTTP API
│   │
│   ├── program/                              # Program microservice
│   │   ├── __init__.py
│   │   └── app.py                            # FastAPI app (:8004) — intent graphs, code gen
│   │
│   ├── rag/                                  # RAG microservice
│   │   ├── __init__.py
│   │   └── app.py                            # FastAPI app (:8003) — vector index + retrieval
│   │
│   ├── routers/                              # FastAPI route handlers (monolith)
│   │   ├── __init__.py
│   │   ├── ai.py                             # AI explanation endpoints
│   │   ├── graph.py                          # Graph analysis + file explorer endpoints
│   │   ├── jobs.py                           # Job queue endpoints
│   │   ├── meta.py                           # Health check + metadata endpoints
│   │   ├── narrator_ws.py                    # Narrator WebSocket endpoints
│   │   ├── program.py                        # Program graph + code generation
│   │   └── rag.py                            # RAG query/index endpoints
│   │
│   ├── services/                             # Business logic layer
│   │   ├── __init__.py
│   │   ├── ai_svc.py
│   │   ├── graph_svc.py
│   │   ├── program_svc.py
│   │   └── rag_svc.py
│   │
│   ├── shared/                               # Shared utilities and infrastructure
│   │   ├── __init__.py
│   │   ├── ai.py                             # LLM interaction (Ollama streaming)
│   │   ├── auth.py                           # Clerk JWT verification
│   │   ├── config.py                         # Environment config loader
│   │   ├── crawler.py                        # GitHub API file crawler
│   │   ├── db.py                             # PostgreSQL connection + queries
│   │   ├── embedding.py                      # Ollama embedding generation
│   │   ├── ingest.py                         # Clone / ZIP extraction
│   │   ├── jobqueue.py                       # Job queue client
│   │   ├── llm_providers.py                  # Multi-provider LLM abstraction
│   │   ├── milvus_service.py                 # Milvus vector DB client
│   │   ├── mongo_service.py                  # MongoDB client
│   │   ├── narrator.py                       # Linear narrator (legacy)
│   │   ├── narrator_graph.py                 # LangGraph interactive narrator
│   │   ├── parser.py                         # tree-sitter multi-language parser
│   │   ├── rag_chain.py                      # LangChain RAG chain builder
│   │   ├── schemas.py                        # Pydantic request/response models
│   │   ├── state.py                          # In-memory shared state
│   │   └── migrations/                       # SQL migration scripts
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
    ├── vercel.json                           # Vercel frontend config
    ├── vite.config.ts
    └── src/
        ├── App.tsx
        ├── CodeMap.css                       # IDE styling (dark theme)
        ├── CodeMap.tsx                       # Main IDE component
        ├── index.css
        ├── main.tsx                          # React entry point + routing
        ├── components/
        │   ├── AuthRequestInterceptor.tsx    # Clerk auth token injection
        │   ├── context.ts                    # Shared React context
        │   ├── EzEdge.tsx                    # Custom React Flow edge renderer
        │   ├── EzNode.tsx                    # Custom React Flow node renderer
        │   ├── FileGroupNode.tsx             # Architect-view file group node
        │   ├── FileItem.tsx                  # File explorer sidebar item
        │   └── LibraryNode.tsx               # 3rd-party dependency node renderer
        ├── config/
        │   └── constants.ts
        ├── context/
        │   └── TourContext.tsx               # Guided tour state provider
        ├── lib/
        │   └── layoutUtils.ts               # Graph layout algorithms (tree + architect)
        ├── pages/
        │   ├── AppLayout.tsx                 # App shell with sidebar nav
        │   ├── ConversationPage.tsx          # RAG conversation UI
        │   ├── LandingPage.tsx               # Auth landing page
        │   ├── MyGraphsPage.tsx              # Saved graphs browser
        │   └── ProtectedRoute.tsx            # Clerk auth guard
        └── types/
            └── index.ts                      # Shared TypeScript type definitions
```

---

## Documentation Index

| Document                                | Description                                      |
|-----------------------------------------|--------------------------------------------------|
| [OVERVIEW.md](OVERVIEW.md)             | This file — system overview                      |
| [BACKEND.md](BACKEND.md)               | Backend architecture, routers, services, shared  |
| [FRONTEND.md](FRONTEND.md)             | Frontend architecture, components, layout        |
| [DATA_LAYER.md](DATA_LAYER.md)         | Databases, schemas, migrations, data flow        |
| [AI_PIPELINE.md](AI_PIPELINE.md)       | AI/LLM, narrator, RAG, code generation           |
| [INFRASTRUCTURE.md](INFRASTRUCTURE.md) | Docker, CI/CD, deployment, environment config    |
| [API_REFERENCE.md](API_REFERENCE.md)   | Complete HTTP + WebSocket API reference           |
