# EzDocs Microservices

The backend can run as a **monolith** (single process) or as **microservices** behind a gateway.

## Layout

| Service   | Port | Responsibility |
|----------|------|----------------|
| **Gateway** | 8000 | Entry point. Serves `/health`, `/explain`, `/ws/explain`, `/ws/chat`, `/ws/narrate`, `/ws/narrate/node` locally; proxies the rest to the services below. |
| **Graph**  | 8001 | Codebase analysis (`/analyze`, `/files`, `/graph`), GitHub/upload, graph persistence (Postgres). |
| **RAG**    | 8003 | RAG query and index (`/rag/*`). Postgres + Milvus + embeddings. |
| **Program**| 8004 | Program graphs, summarization, code generation (`/program`, `/generate`, `/generated`). Postgres + MongoDB + LLM. |

AI and narrator run in the **gateway** process so WebSockets work without a separate proxy. Graph, RAG, and Program are separate processes; the gateway forwards HTTP requests to them.

## Run all (microservices)

From repo root, with Postgres, MongoDB, Milvus (and optionally Ollama) available:

```bash
# Terminal 1 – Graph
cd backend && uvicorn graph.app:app --host 0.0.0.0 --port 8001

# Terminal 2 – RAG
cd backend && uvicorn rag.app:app --host 0.0.0.0 --port 8003

# Terminal 3 – Program
cd backend && uvicorn program.app:app --host 0.0.0.0 --port 8004

# Terminal 4 – Gateway (single entry for frontend)
cd backend && python gateway.py
# or: uvicorn gateway.app:app --host 0.0.0.0 --port 8000
```

Frontend: set `VITE_API_URL=http://localhost:8000` (and leave `WS_BASE` derived from it). All traffic goes to the gateway.

## Run monolith (single process)

```bash
cd backend && python main.py
```

One process serves every route. No gateway, no proxy. Use this for local development if you don’t need separate services.

## Narrator in microservices mode

The narrator needs the current codebase graph. In the monolith it uses in-memory cache. In microservices the graph is built in the **graph** service; the gateway (which runs the narrator) has no cache.

So when using the gateway + services:

1. **Analyze** with a `codebase_id` so the graph is stored in Postgres:  
   `GET /analyze?path=...&codebase_id=<uuid>`  
   The response includes `codebase_id` when you passed it.
2. **Start Tour**: open `/ws/narrate` and send a first message:  
   `{"codebase_id": "<same-uuid>"}`  
   The narrator loads the graph from Postgres and runs the tour.

If the frontend generates a `codebase_id` (e.g. `crypto.randomUUID()`) and passes it to `analyze`, it can reuse it when opening the narrator WebSocket.

## Environment (microservices)

- **Gateway and all services** use the same `backend/.env` (same Postgres, MongoDB, Milvus, Ollama, Clerk).
- Optional overrides for service URLs (e.g. when services run in Docker):
  - `EZDOCS_GRAPH_SVC_URL=http://graph:8001`
  - `EZDOCS_RAG_SVC_URL=http://rag:8003`
  - `EZDOCS_PROGRAM_SVC_URL=http://program:8004`

## WebSocket not proxied

The gateway does **not** proxy WebSockets. So:

- `/ws/analyze/github` (streaming GitHub analysis) is **not** forwarded to the graph service. In microservices mode either call the graph service directly (e.g. `ws://localhost:8001/ws/analyze/github`) from the frontend, or run the monolith for that flow.

## Docker Compose (optional)

With Postgres, MongoDB, and Milvus already defined in `docker-compose.yml`:

```bash
docker compose -f docker-compose.yml -f docker-compose.microservices.yml up -d
```

This starts graph (8001), rag (8003), program (8004), and gateway (8000). The gateway proxies to the three services. Point the frontend at `http://localhost:8000`.

**Folder layout (by microservice):**
- `shared/` — config, db, schemas, auth, state, jobqueue, parser, migrations, **ai, narrator, crawler, ingest, milvus_service, mongo_service, embedding, llm_providers** (used by all services)
- `graph/` — Graph service app (`graph.app:app`)
- `rag/` — RAG service app (`rag.app:app`)
- `program/` — Program service app (`program.app:app`)
- `gateway/` — Gateway app (`gateway.app:app`); proxies + AI + narrator
- `jobs/` — Option C job queue router and handlers
- `routers/` — FastAPI routers (graph, rag, program, ai, meta, narrator_ws, jobs)
- `services/` — Optional service entry points (`services.graph_svc:app`, `services.rag_svc:app`, etc.)
- Root: `main.py` (monolith), `gateway.py` (gateway launcher); root `ai.py`, `narrator.py`, `crawler.py`, `ingest.py`, `milvus_service.py`, `mongo_service.py`, `embedding.py`, `llm_providers.py`, `config`, `db`, `auth`, `state`, `jobqueue`, `schemas`, `parser` are **shims** that re-export from `shared` so existing imports keep working.

Ollama (for AI/narrator) is not in the stack; run it on the host and set `OLLAMA_HOST=http://host.docker.internal:11434` for the gateway container (already set in the compose).
