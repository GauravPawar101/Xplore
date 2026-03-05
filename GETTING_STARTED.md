# EzDocs — What You Need and Where to Put It

You have **Postgres** and **MongoDB** (Compass). Below: what else to get, and where to configure everything.

**Fixed ports (CORS is tuned for these):**

| Service   | Port(s)        | Notes |
|-----------|----------------|-------|
| Frontend  | **5173** (fallback 5174–5177) | Vite tries 5173 first; if busy, uses next. Backend allows only these origins. |
| Backend API (monolith or gateway) | **8000** | Set `EZDOCS_PORT` to change. |
| Graph (microservice) | 8001 | `EZDOCS_PORT_GRAPH` |
| RAG       | 8003 | `EZDOCS_PORT_RAG` |
| Program   | 8004 | `EZDOCS_PORT_PROGRAM` |
| AI        | 8002 | `EZDOCS_PORT_AI` |

---

## 1. What you already have

| Service   | You have it        | Used for                                      |
|----------|--------------------|-----------------------------------------------|
| Postgres | Installed          | Users, analyses, codebase graph, program graphs |
| MongoDB  | Installed (Compass)| Storing generated code (≤ 5MB per run)       |

---

## 2. What you need to get

### Milvus (required for vector RAG)

EzDocs uses Milvus to store **embeddings** for the graph so RAG search is semantic.

**Option A — Docker (easiest)**

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) if you don’t have it.
2. Run:
   ```bash
   docker run -d --name milvus -p 19530:19530 -p 9091:9091 milvusdb/milvus:latest
   ```
3. Milvus will be at `http://localhost:19530`. No extra config.

**Option B — Standalone**

1. Download: https://milvus.io/docs/install_standalone-docker.md (or use Docker as above).
2. Start Milvus so it listens on port **19530**.

### Ollama (optional but recommended for AI)

For **explanations** and **narrator** in the UI:

1. Install: https://ollama.ai
2. In a terminal: `ollama pull qwen2.5-coder:3b`
3. Keep Ollama running (it serves on `http://127.0.0.1:11434` by default).

### Clerk (required for sign-in and auth)

Auth is required: the app shows **Sign in / Sign up** on the landing page and protects the app routes.

1. Create an app at https://dashboard.clerk.com
2. **Frontend:** Create `frontend/.env.local` and set:
   ```env
   VITE_CLERK_PUBLISHABLE_KEY=pk_test_...   # from Clerk Dashboard → API keys → Publishable Key (React)
   ```
   The app will not start without this key.
3. **Backend:** In `backend/.env` set:
   ```env
   CLERK_JWKS_URL=https://your-app.clerk.accounts.dev/.well-known/jwks.json
   ```
   Get this from Clerk Dashboard → Frontend API → **JWKS URL** (or append `/.well-known/jwks.json` to your Frontend API URL). The API uses this to verify the JWT on every request.
4. **Clerk Dashboard:** Add your frontend URL so sign-in and CORS work:
   - Add **http://localhost:5173** through **http://localhost:5177** (and the same for **http://127.0.0.1:5173**–**5177**) to allowed redirect URLs and origins. The app uses these fixed ports so CORS stays consistent.

---

## 3. Run all services with Docker

If you prefer to run **Postgres**, **MongoDB**, and **Milvus** in Docker (no local installs):

1. Install [Docker Desktop](https://www.docker.com/products/docker-desktop/).
2. From the repo root:
   ```bash
   docker compose up -d
   ```
3. This starts:
   - **Postgres** on `localhost:5432` with database **`ezdocs`** (created automatically).
   - **MongoDB** on `localhost:27017`.
   - **Milvus** on `http://localhost:19530`.

Use the same **`backend/.env`** as in section 4 (`DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ezdocs`, `MONGODB_URI=mongodb://localhost:27017`, `MILVUS_URI=http://localhost:19530`). Run the backend and frontend on your machine (section 5).

To stop: `docker compose down`. Data is kept in Docker volumes until you remove them.

---

### Postgres: create the database

**If you use Docker (section 3):** the `ezdocs` database is created automatically. Skip to section 4.

**If you run Postgres yourself:**

1. Open **pgAdmin** (or `psql`) and connect to your Postgres server.
2. Create a database named **`ezdocs`**.
3. If your Postgres user/password are not `postgres` / `postgres`, you’ll set the URL in `.env` (step 4).

The app runs the schema (tables) automatically on first backend start.

### MongoDB: nothing to create

- Make sure the **MongoDB server** is running (same one you use with Compass).
- The app uses database **`ezdocs`** and collection **`generated_code`** and creates them on first use.
- Default connection: `mongodb://localhost:27017`. If yours is different, set `MONGODB_URI` in `.env`.

### Milvus: run it

- Start Milvus (Docker or standalone) so it’s reachable at **`http://localhost:19530`** (see section 2).

---

## 4. Where to put config — `backend/.env`

1. Go to the **backend** folder:
   ```bash
   cd backend
   ```
2. Copy the example env file:
   - **Windows:** `copy .env.example .env`
   - **Mac/Linux:** `cp .env.example .env`
3. Open **`backend/.env`** in an editor and set only what you need:

| Variable        | Where to get it / what to put |
|-----------------|-------------------------------|
| `DATABASE_URL`  | Postgres connection string. Default: `postgresql://postgres:postgres@localhost:5432/ezdocs` — change `postgres:postgres` if your user/password are different, and use the DB name you created (`ezdocs`). |
| `MONGODB_URI`  | MongoDB connection. Default: `mongodb://localhost:27017`. If MongoDB is on another host/port, set it here. |
| `MILVUS_URI`   | Milvus address. Default: `http://localhost:19530`. Set if Milvus runs elsewhere. |
| `OLLAMA_HOST`  | Only if Ollama is not on the same machine. Default: `http://127.0.0.1:11434`. |
| `OPENAI_API_KEY` / `ANTHROPIC_API_KEY` | Optional. If you set one, the app can use it for summaries/code gen when the user doesn’t send their own key. |
| `CLERK_JWKS_URL` | **Required for auth.** Clerk JWKS URL so the API can verify JWTs. From Clerk Dashboard → Frontend API → `.well-known/jwks.json`. |
| `UPSTASH_REDIS_REST_URL` / `UPSTASH_REDIS_REST_TOKEN` | Optional. **Option C** (job queue): create a Redis database at [Upstash](https://console.upstash.com) (free tier), then set both. Analysis then runs via queue + in-process worker; frontend polls `GET /jobs/:id/status` and `GET /jobs/:id/result`. Without these, analysis stays synchronous (GET /analyze or WebSocket). |

Example **`backend/.env`** with all services (adjust user/pass if needed):

```env
# Postgres (you have it)
DATABASE_URL=postgresql://postgres:postgres@localhost:5432/ezdocs

# MongoDB (you have it)
MONGODB_URI=mongodb://localhost:27017

# Milvus (you need to run it — see section 2)
MILVUS_URI=http://localhost:19530

# Optional: Ollama for AI
# OLLAMA_HOST=http://127.0.0.1:11434

# Optional: API keys for summaries/code gen
# OPENAI_API_KEY=sk-...
# ANTHROPIC_API_KEY=sk-ant-...

# Clerk auth (required for API to verify JWTs)
CLERK_JWKS_URL=https://your-app.clerk.accounts.dev/.well-known/jwks.json

# Optional: Option C job queue (Upstash Redis — see docs/OPTION_C_FREE_TIER.md)
# UPSTASH_REDIS_REST_URL=https://xxx.upstash.io
# UPSTASH_REDIS_REST_TOKEN=AXxx...
```

Save the file. All config lives in **`backend/.env`**; nothing else to “put” for these services.

---

## 5. Run the app

1. **Start Postgres** (if not running as a service).
2. **Start MongoDB** (if not running).
3. **Start Milvus** (Docker or standalone, port 19530).
4. **Backend:**
   ```bash
   cd backend
   pip install -r requirements.txt
   python main.py
   ```
   → Backend: http://localhost:8000  
   → API docs: http://localhost:8000/docs  
5. **Frontend:**
   ```bash
   cd frontend
   npm install
   npm run dev
   ```
   → Open the URL shown (e.g. http://localhost:5175).

---

## 6. Quick checklist

| Step | What to do |
|------|------------|
| 1 | **Option A:** Run `docker compose up -d` in the repo root (Postgres + MongoDB + Milvus; database `ezdocs` is created). **Option B:** Install Postgres/MongoDB/Milvus yourself and create database **`ezdocs`** in Postgres. |
| 2 | Copy **`backend/.env.example`** to **`backend/.env`** and set `DATABASE_URL`, `MONGODB_URI`, `MILVUS_URI` (defaults work with Docker from step 1). |
| 3 | Run **backend**: `cd backend && python main.py`. |
| 4 | Run **frontend**: `cd frontend && npm run dev`. |
| 5 | (Optional) Install **Ollama** and run `ollama pull qwen2.5-coder:3b` for AI explanations. |
| 6 | **Clerk (required):** Set `VITE_CLERK_PUBLISHABLE_KEY` in `frontend/.env.local` (Publishable Key from Clerk). Set `CLERK_JWKS_URL` in `backend/.env` (JWKS URL from Clerk Dashboard). In Clerk Dashboard, add your frontend URL (e.g. `http://localhost:5177`) to allowed redirect URLs and origins. |

Nothing is optional in the codebase: all services are implemented. The only thing you must **get** in addition to Postgres and MongoDB is **Milvus**; everything else is “where to put” config in **`backend/.env`** and creating the **`ezdocs`** database in Postgres.

---

## 7. Option C — Job queue (deployable)

To run **Option C** (event-driven workers + queue) so analysis is non-blocking and deployable on free tier:

1. **Queue:** Create a Redis database at [Upstash](https://console.upstash.com) (free tier). In **backend/.env** set:
   - `UPSTASH_REDIS_REST_URL` — REST URL from the Upstash console
   - `UPSTASH_REDIS_REST_TOKEN` — REST token from the console  
   Backend starts a **worker thread** in the same process as the API; it pulls jobs from Upstash and runs graph analysis (and optionally RAG/code gen later).

2. **Frontend:** Uses `POST /jobs/analyze` when the queue is available (returns `job_id`), then polls `GET /jobs/:id/status` until done and fetches `GET /jobs/:id/result`. If the queue is not configured (503), the frontend falls back to synchronous `GET /analyze` or WebSocket for GitHub.

3. **Deploy:** Frontend on **Vercel** or **Netlify**; backend (single app = API + worker) on **Railway**, **Render**, or **Fly.io**. Set `VITE_API_URL` in the frontend build to your backend URL. See **docs/OPTION_C_FREE_TIER.md** for platform notes (Render spin-down, Fly memory limits, cron ping to keep Render awake).

---

## Troubleshooting CORS and 400

- **CORS:** Restart the backend after changing `.env` or CORS config. The default allows any origin (`^https?://.+$`). For production, set `EZDOCS_CORS_ORIGIN_REGEX=""` in `backend/.env` and use `EZDOCS_CORS_ORIGINS` with your frontend URL(s).
- **400 Bad Request:** In the browser open DevTools → **Network**, click the failed request, and open the **Response** tab. The body shows the validation error (e.g. missing `path`, invalid GitHub URL).
