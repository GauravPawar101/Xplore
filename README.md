# Xplore

Code dependency visualizer with AI-powered explanations and guided narration.

**Setup (what to get and where to put it):** see **[GETTING_STARTED.md](GETTING_STARTED.md)** — you have Postgres and MongoDB; you need Milvus and `backend/.env`.

## What you need to do (from scratch)

If you haven’t touched the codebase yet, follow this.

### 1. Install dependencies

**Backend**
```bash
cd backend
pip install -r requirements.txt
```

**Frontend**
```bash
cd frontend
npm install
```

### 2. Start the app

**Terminal 1 — backend**
```bash
cd backend
python main.py
```
Backend runs at **http://localhost:8000**.

**Terminal 2 — frontend**
```bash
cd frontend
npm run dev
```
Frontend runs at **http://localhost:5173** or **http://localhost:5175** (see terminal output).

### 3. Use the app

1. Open the frontend URL in your browser (e.g. http://localhost:5175).
2. **Analyze** a codebase:
   - Enter a **local path** (e.g. `C:\Users\You\projects\myrepo` or `./backend`), or
   - Paste a **GitHub repo URL**, or
   - **Upload** a `.zip` of the code.
3. Explore the **dependency graph**, switch **Arch / Func** view, click nodes to **focus** and **expand**.
4. Select a node → **Generate** for an AI explanation; use **Start Tour** for a narrated walkthrough (needs Ollama running locally).

**No config required** for this. The graph is kept in memory; AI uses local Ollama if available.

### 4. Optional: persistence and extra features

To **save** analyses, use **program graphs**, **RAG**, or **generated code**, add a `backend/.env` file:

```bash
cd backend
copy .env.example .env
# Edit .env and set only what you need:
```

| You want… | Set in `backend/.env` |
|-----------|------------------------|
| Save graphs & program graphs | `DATABASE_URL=postgresql://user:pass@localhost:5432/ezdocs` (and run Postgres) |
| Save generated code (5MB limit) | `MONGODB_URI=mongodb://localhost:27017` (and run MongoDB) |
| Vector RAG (semantic search) | `MILVUS_URI=http://localhost:19530` (and run Milvus) |
| AI explanations without Ollama | `OPENAI_API_KEY=sk-...` or `ANTHROPIC_API_KEY=sk-ant-...` |
| User sign-in (Clerk) | `CLERK_JWKS_URL=https://<your-app>.clerk.accounts.dev/.well-known/jwks.json` |

If you don’t set these, the app still runs; features that need a service will return an error or “unavailable” until you configure them.

### 5. Optional: Ollama (for AI explanations and narrator)

1. Install [Ollama](https://ollama.ai).
2. Run `ollama pull qwen2.5-coder:3b` (or the model set in `EZDOCS_MODEL`).
3. Keep Ollama running; the backend uses `http://127.0.0.1:11434` by default.

---

## Structure

```
EzDocs/
├── .env.example          # Copy to backend/.env and configure
├── .gitignore
├── README.md
├── docs/
│   └── ARCHITECTURE.md    # High-level design and data flow
├── scripts/               # Optional: start/setup scripts
├── backend/               # FastAPI Python service
│   ├── main.py            # App entry, middleware, router registration
│   ├── config.py          # Settings from environment
│   ├── schemas.py         # Pydantic request/response models
│   ├── state.py           # In-memory graph cache and parser singleton
│   ├── requirements.txt
│   ├── routers/           # Route modules
│   │   ├── meta.py        # Health
│   │   ├── graph.py       # Analyze (local/GitHub/upload), files, WS GitHub
│   │   ├── ai.py          # Explain, WS explain
│   │   └── narrator_ws.py # WS narrate, WS narrate/node
│   ├── graph.py           # GraphBuilder
│   ├── parser.py          # UniversalParser
│   ├── ai.py              # Ollama integration
│   ├── narrator.py        # Tour and node narration logic
│   ├── ingest.py          # Clone and upload handling
│   └── crawler.py         # GitHub streaming crawler
└── frontend/              # Vite + React + TypeScript
    ├── index.html
    ├── package.json
    ├── vite.config.ts
    ├── tsconfig.json
    └── src/
        ├── main.tsx
        ├── App.tsx
        ├── CodeMap.tsx    # Main graph UI and narration panels
        ├── CodeMap.css
        ├── index.css      # Global styles
        ├── config/        # Constants and app config
        │   └── constants.ts
        ├── lib/           # Utilities
        │   └── layoutUtils.ts
        ├── types/         # Shared TypeScript types
        │   └── index.ts
        └── components/    # Reusable UI
            ├── context.ts
            ├── EzNode.tsx
            ├── EzEdge.tsx
            ├── FileGroupNode.tsx
            └── FileItem.tsx
```

## Prerequisites

- Python 3.10+
- Node.js 18+
- [Ollama](https://ollama.ai) (for AI explanations and narrator)

## Setup

Same as **What you need to do** above. Quick copy:

- **Backend**: `cd backend && pip install -r requirements.txt`  
  Optional: `copy .env.example .env` and set `DATABASE_URL`, `MONGODB_URI`, etc.
- **Frontend**: `cd frontend && npm install`

## Run

**Option 1 — Windows script (from repo root)**  
```bash
start.bat
```

**Option 2 — Manual**

- Terminal 1 (backend): `cd backend && python main.py`
- Terminal 2 (frontend): `cd frontend && npm run dev`

- Frontend: http://localhost:5175 (or 5173)
- Backend API: http://localhost:8000  
- Health: http://localhost:8000/health

## Usage

1. **Analyze** — Local path, GitHub URL, or zip upload → dependency graph.
2. **Explore** — Click nodes to focus, use Arch/Func view, expand file groups.
3. **AI** — Select a node → Generate explanation; optional voice narration.
4. **Tour** — “Start Tour” for a narrated codebase walkthrough; “Explain This Node” for a deep-dive on the selected node.

## Configuration

- **Backend**: `backend/.env` (see `.env.example`).  
  `EZDOCS_MODEL`, `EZDOCS_BATCH_CONCURRENCY`, `EZDOCS_INGEST_DIR`, `EZDOCS_MAX_FILES`, etc.
- **Frontend**: Vite dev server port in `frontend/vite.config.ts`.

## Supported languages

Python, JavaScript/TypeScript, Java, Rust (via tree-sitter in the backend).

## License

MIT
# Xplore
# Xplore
