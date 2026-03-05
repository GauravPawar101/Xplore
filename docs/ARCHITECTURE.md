# EzDocs Architecture

## Overview

EzDocs is a full-stack application with **two main ways** to use it: (1) **Analyze & learn** — user provides links/code, explores the graph, gets a tour or asks conversationally; (2) **Create graph → code generation** — user builds a graph (nodes: files, folders, encapsulation), stores it, and generates code (stored in MongoDB, ≤5MB; downloadable). Users sign in with **Clerk**; **Postgres** holds user→analyses and user→graphs; **MongoDB** holds generated code. See [Product & Data Architecture](PRODUCT_AND_DATA_ARCHITECTURE.md) for flows, stores, and **where to configure what** (.env, Clerk, Postgres, MongoDB, Neo4j).

The backend is a FastAPI service; the frontend is a React (Vite + TypeScript) SPA.

## High-Level Flow

1. **Analysis** — User provides a path, GitHub URL, or zip upload. Backend parses supported languages, builds a dependency graph (nodes = symbols, edges = calls), and returns a React Flow–compatible JSON graph.
2. **Exploration** — Frontend renders the graph, supports focus mode, file explorer, and node selection.
3. **AI** — Selected node code (and optional context) is sent to Ollama; responses are streamed over WebSocket or returned in one shot. Narrator streams a guided tour with optional TTS.

## Backend

- **Entry**: `main.py` — FastAPI app, CORS, GZip, routers.
- **Routers**: REST and WebSocket routes live under `routers/` (graph, AI, narrator, meta).
- **Core**: `graph.py` (GraphBuilder), `parser.py` (UniversalParser), `ai.py` (Ollama), `narrator.py` (tour + node narration), `ingest.py` (clone/upload), `crawler.py` (GitHub streaming).
- **Config**: `config.py` — settings from environment.
- **Schemas**: `schemas.py` — Pydantic request/response models.

## Frontend

- **Entry**: `main.tsx` → `App.tsx` → main feature (CodeMap).
- **Features**: CodeMap (graph, sidebar, panels, narrator) in `src/`.
- **Components**: Reusable UI in `src/components/` (EzNode, EzEdge, FileGroupNode, FileItem, context).
- **Lib / Config / Types**: Utilities, constants, and shared types under `src/lib/`, `src/config/`, `src/types/`.
- **Styles**: Global and component-level CSS; Tailwind + custom variables.

## Data Flow

- **Graph**: Backend builds graph in memory; cached after analysis; WebSocket narration reads from same cache.
- **AI**: Ollama runs locally; model and concurrency are configurable via env.
- **TTS**: Browser Speech Synthesis API; voice and rate tuned for natural narration.

## Conventions

- Backend: run from `backend/`; `python main.py` or `uvicorn main:app`.
- Frontend: run from `frontend/`; `npm run dev` (Vite).
- Environment: copy `.env.example` to `backend/.env` and set variables as needed.

## See also

- [Product & Data Architecture](PRODUCT_AND_DATA_ARCHITECTURE.md) — two flows, Clerk, Postgres, MongoDB, and **where to configure what** (.env, dashboards, DBs).
- [Graph DB + RAG feasibility](GRAPH_DB_AND_RAG_FEASIBILITY.md) — storing tree-sitter parse format and summaries in a DB, frontend querying backend/DB, and RAG over the codebase.
- [Neo4j + RAG + Code Generation](NEO4J_RAG_AND_CODE_GEN.md) — Neo4j as graph store, program-as-graph (user-defined nodes + summarization), code generation, and user API keys.
