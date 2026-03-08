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
   ┌────┴────┐
   ▼         ▼
┌────────┐ ┌────────┐
│ Ollama │ │Upstash │
│  LLM   │ │ Redis  │
│ Local  │ │ Jobs   │
└────────┘ └────────┘
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
| Job Queue       | Upstash Redis (HTTP-based)                                    |
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
├── arch/                        # Architecture documentation (this folder)
├── backend/
│   ├── main.py                  # Monolith entry point
│   ├── gateway.py               # Microservice gateway entry
│   ├── gateway/app.py           # Gateway FastAPI app
│   ├── graph/                   # Graph microservice
│   │   ├── app.py               # FastAPI app (:8001)
│   │   └── builder.py           # Core graph construction engine
│   ├── rag/app.py               # RAG microservice (:8003)
│   ├── program/app.py           # Program microservice (:8004)
│   ├── jobs/
│   │   ├── handlers.py          # Background job workers
│   │   └── router.py            # Job queue HTTP API
│   ├── routers/                 # FastAPI route handlers
│   │   ├── __init__.py
│   │   ├── ai.py                # AI explanation endpoints
│   │   ├── graph.py             # Graph analysis endpoints
│   │   ├── rag.py               # RAG query/index endpoints
│   │   ├── program.py           # Program graph + code gen
│   │   ├── jobs.py              # Job queue endpoints
│   │   └── narrator_ws.py       # Narrator WebSocket endpoints
│   ├── services/                # Business logic layer
│   │   ├── ai_svc.py            # AI service
│   │   ├── graph_svc.py         # Graph service
│   │   ├── program_svc.py       # Program service
│   │   └── rag_svc.py           # RAG service
│   ├── shared/                  # Shared utilities and infrastructure
│   │   ├── ai.py                # LLM interaction (Ollama streaming)
│   │   ├── auth.py              # Clerk JWT verification
│   │   ├── config.py            # Environment config loader
│   │   ├── crawler.py           # GitHub API file crawler
│   │   ├── db.py                # PostgreSQL connection + queries
│   │   ├── embedding.py         # Ollama embedding generation
│   │   ├── ingest.py            # Clone / ZIP extraction
│   │   ├── jobqueue.py          # Upstash Redis job queue
│   │   ├── llm_providers.py     # Multi-provider LLM abstraction
│   │   ├── milvus_service.py    # Milvus vector DB client
│   │   ├── mongo_service.py     # MongoDB client
│   │   ├── narrator.py          # Linear narrator (legacy)
│   │   ├── narrator_graph.py    # LangGraph interactive narrator
│   │   ├── parser.py            # tree-sitter multi-language parser
│   │   ├── rag_chain.py         # LangChain RAG chain builder
│   │   ├── schemas.py           # Pydantic request/response models
│   │   ├── state.py             # In-memory shared state
│   │   └── migrations/          # SQL migration scripts
│   ├── api/                     # Vercel serverless entry points
│   ├── tests/                   # pytest test suite
│   ├── requirements.txt         # Python dependencies
│   ├── Dockerfile               # Backend Docker image
│   └── railway.json             # Railway deployment config
├── frontend/
│   ├── src/
│   │   ├── main.tsx             # React entry point + routing
│   │   ├── CodeMap.tsx          # Main IDE component (~2000 lines)
│   │   ├── CodeMap.css          # IDE styling (dark theme)
│   │   ├── pages/
│   │   │   ├── AppLayout.tsx    # App shell with nav
│   │   │   └── LandingPage.tsx  # Auth landing page
│   │   ├── components/
│   │   │   └── LibraryNode.tsx  # 3rd-party node renderer
│   │   └── lib/
│   │       └── layoutUtils.ts   # Graph layout algorithms
│   ├── package.json
│   └── vercel.json              # Vercel frontend config
├── docker-compose.yml           # Infrastructure services
├── docker-compose.microservices.yml  # Full stack in Docker
├── start.bat                    # Windows setup + launcher
└── .github/workflows/ci.yml    # CI pipeline
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
