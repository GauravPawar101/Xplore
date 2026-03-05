# EzDocs Product & Data Architecture

This document describes the **two ways** users use EzDocs, how **auth and data** are organized (Clerk + **Postgres** + **MongoDB** + **Milvus**), and **where to configure what** (env, dashboards, DBs).

---

## 1. Two ways to use EzDocs

### Flow A: Analyze & learn

- User provides **links** (e.g. GitHub repo) or **code** (upload/path) to analyze.
- Backend parses the codebase, builds a **dependency graph** (nodes = symbols, edges = calls), and generates **summaries per function** (the main server-side “heavy lifting”).
- User can:
  - **Interact** with the graph (explore, focus, file view), or
  - **Get a tour** (narrator), or
  - **Ask conversationally** (chat over the codebase).
- All of this is tied to the **signed-in user** (Clerk): analyses are stored and listed in the dashboard; user can revisit previous analyses.

### Flow B: Create graph → code generation

- User **creates a graph** in the dashboard:
  - **Nodes** can represent: “create separate files”, “folders”, “encapsulate X”, or general intent (e.g. “auth module”, “API layer”).
  - Graph is **general graph creation** (nodes + edges), stored and associated with the user.
- User triggers **code generation** (target language/stack, with their API key).
- Generated **code** is **stored** (e.g. in MongoDB) with a **5MB size limit**.
- User can **download** the generated code or **keep it** in the app and view it from the dashboard.

---

## 2. Auth: Clerk

- **Sign-in**: Each user signs in via **Clerk** (OAuth / email / etc.).
- **Identity**: User has a stable **Clerk user id** (and optionally email, name, etc.).
- **Association**: All **analyses** and **program graphs** (and later **generated code** references) are associated with this user id. Dashboard and API filter by “current user”.

---

## 3. Data stores and server role

| Store | Purpose |
|-------|--------|
| **Postgres** | **User ↔ graph (and analyses).** Users (Clerk id, profile), list of **analyses** (user_id, codebase_id, source path/url, created_at, graph snapshot or reference), list of **program graphs** (user_id, program_id, name, nodes, edges, created_at). Optionally: codebase graph blobs (nodes/edges as JSONB) and symbol summaries. So “user → graph” and all app metadata live in Postgres. |
| **MongoDB** | **Generated code only.** Each generated artifact: user_id, program_id, generation_id, timestamp, and the **code** (e.g. map of path → content, or a single document). **Max size 5MB** per generation (or per user policy). User can download or keep. |
| **Server** | Does **not** do heavy lifting except: (1) **Generating summaries** for each function (during analysis), (2) **Hosting/connecting to DBs** (Postgres, MongoDB, and optionally Neo4j if used for RAG). Parsing and graph build can stay; code generation uses the user’s API key and runs on the user’s chosen provider. |

**Optional:** Vector RAG uses **Milvus** to store embeddings per (codebase_id, symbol_id). Call `POST /rag/index?codebase_id=` after analysis (and optionally after summaries) to enable semantic search.

---

## 4. Graph creation (Flow B) in the UI

- **Nodes** can be named/typed, e.g.:
  - “Create separate file” (node represents one file),
  - “Folder” (node represents a directory),
  - “Encapsulate” (module/namespace),
  - General “component” or “step” with free-form content.
- User draws **edges** between nodes (e.g. “A depends on B”, “B lives in folder C”).
- Graph is **saved** to Postgres (user_id, program_id, nodes, edges).
- For **code generation**, the backend uses this graph (and optional summarization step) plus the user’s model/API key; output is written to **MongoDB** (≤ 5MB) and linked to the user and program.

---

## 5. Dashboard and pages

- **Analysis**: Page(s) to start an analysis (paste link, upload zip, or path), see progress, and open the result (graph view, tour, chat).
- **History**: List of **previous analyses** for the signed-in user (from Postgres).
- **Create graph**: Page(s) to create and edit **program graphs** (nodes: files, folders, encapsulation, general; edges), save, and trigger code generation.
- **Generated code**: List of **generations** for the user; view or **download**; stored in MongoDB (5MB limit enforced on write).

---

## 6. Where to configure what

Use this as a checklist: where you set env vars, create resources, or configure dashboards.

### 6.1 Backend `.env` (e.g. `backend/.env`)

Create from `backend/.env.example`. Set:

| Variable | Where / what |
|----------|----------------|
| **Clerk** | `CLERK_PUBLISHABLE_KEY`, `CLERK_SECRET_KEY` (from Clerk Dashboard → API Keys). Optional: `CLERK_WEBHOOK_SECRET` if you verify webhooks. |
| **Postgres** | `DATABASE_URL` (e.g. `postgresql://user:password@host:5432/ezdocs`). Used for users, analyses, program graphs. |
| **MongoDB** | `MONGODB_URI` (e.g. `mongodb://localhost:27017` or Atlas connection string). Used for generated code storage; enforce 5MB limit in app. |
| **Milvus** (optional) | `MILVUS_URI` (e.g. `http://localhost:19530`) for vector RAG. If unset, RAG uses keyword-only (Postgres). |
| **Ollama / LLM** | `OLLAMA_HOST` (if using local Ollama for summaries/narrator). For code gen, user supplies API key; optional server defaults: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`. | `OLLAMA_HOST` (if using local Ollama for summaries/narrator). For code gen, user supplies API key; optional server defaults: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`. |
| **App** | `EZDOCS_HOST`, `EZDOCS_PORT`, `EZDOCS_CORS_ORIGINS` if needed. |

### 6.2 Frontend `.env` (e.g. `frontend/.env`)

| Variable | Where / what |
|----------|----------------|
| **Clerk** | `VITE_CLERK_PUBLISHABLE_KEY` (from Clerk Dashboard → API Keys; same as “Publishable key”). |
| **API** | `VITE_API_URL` (backend base URL, e.g. `http://localhost:8000`) so the frontend calls the correct server. |

### 6.3 Clerk Dashboard (clerk.com)

- Create an **Application**.
- **API Keys**: copy Publishable key → `VITE_CLERK_PUBLISHABLE_KEY`; Secret key → `CLERK_SECRET_KEY` in backend.
- **Settings**: Configure sign-in methods (e.g. Google, email).
- **Paths**: Set redirect/sign-in/sign-up URLs to match your app (e.g. `/sign-in`, `/sign-up`, `/dashboard`).
- Optional: **Webhooks** → endpoint for user created/updated if you sync users to Postgres.

### 6.4 Postgres

- **Where**: Local install, or managed (e.g. Neon, Supabase, RDS). Create a database (e.g. `ezdocs`).
- **Connection string** → `DATABASE_URL` in backend `.env`.
- **Schema**: Run migrations (or SQL) to create tables, e.g. `users` (Clerk id, email, …), `analyses` (user_id, codebase_id, source, graph_snapshot or ref, created_at), `program_graphs` (user_id, program_id, name, nodes, edges, created_at). Optionally `symbol_summaries` if you store summaries in Postgres.

### 6.5 MongoDB

- **Where**: Local install or Atlas. Create a database (e.g. `ezdocs`) and a collection for generated code (e.g. `generated_code`).
- **Connection string** → `MONGODB_URI` in backend `.env`.
- **In app**: On each write of generated code, check total size (e.g. sum of file lengths or single doc size) and **reject or truncate if > 5MB**. Index by `user_id`, `program_id`, `created_at` for listing and download.

### 6.5 Milvus (vector DB for embeddings)

- **Where**: Local install or Zilliz Cloud. Default port 19530.
- **Connection** → `MILVUS_URI` (e.g. `http://localhost:19530`) in backend `.env`.
- **Use**: Store embeddings for graph nodes (codebase_id, symbol_id). After analysis, call `POST /rag/index?codebase_id=` to populate. RAG uses both Postgres (keyword) and Milvus (vector) when `use_vector=true`.

### 6.6 Clerk (auth)

- **Dashboard**: Create application; get Publishable key (frontend) and JWKS URL for backend.
- **Backend**: Set `CLERK_JWKS_URL` (e.g. `https://<your-clerk>.clerk.accounts.dev/.well-known/jwks.json`) in backend `.env`. Optional: if unset, auth is skipped and `user_id` can be passed in request body.
- **Frontend**: Set `VITE_CLERK_PUBLISHABLE_KEY`; wrap app with ClerkProvider.

## 7. Summary table

| You want to… | Do this |
|--------------|--------|
| Run backend | Set `backend/.env` (at least `DATABASE_URL`, `MONGODB_URI`; optional `MILVUS_URI`, `CLERK_JWKS_URL`, Ollama). |
| Run frontend | Set `frontend/.env` (`VITE_CLERK_PUBLISHABLE_KEY`, `VITE_API_URL`). |
| Let users sign in | Configure Clerk app in Clerk Dashboard; set `CLERK_JWKS_URL` in backend, `VITE_CLERK_PUBLISHABLE_KEY` in frontend. |
| Store user ↔ analyses & graphs | Create Postgres DB, run migrations, set `DATABASE_URL`. |
| Store generated code (≤ 5MB) | Create MongoDB DB/collection, set `MONGODB_URI`; enforce 5MB in code. |
| Vector RAG (embeddings) | Set `MILVUS_URI`; call `POST /rag/index?codebase_id=` after analysis to index embeddings. |

---

## 8. See also

- [Architecture](ARCHITECTURE.md) — backend/frontend layout.
- [Neo4j + RAG + Code Generation](NEO4J_RAG_AND_CODE_GEN.md) — program graph, summarization, code gen, API keys (implementation now uses Postgres + Milvus).
- [Graph DB + RAG feasibility](GRAPH_DB_AND_RAG_FEASIBILITY.md) — storage options and phases.
