# EzDocs: Containers, Multi-Platform, Conversation Service, and Protocols

This doc lays out **4–5 ways** to combine:

1. **Running in containers (Docker)** and deploying compute-heavy services on **different platforms**
2. **Further optimizations** (beyond current microservices)
3. **Using an external “conversation” service** (open-source) so chat is less robotic than the built-in Ollama flow
4. **Replacing or complementing JSON** with **protocols better suited to each use case**

**Constraint:** Free hosting (e.g. **Vercel**, **Netlify**) will be used — see **§9** and **§10** for what runs where and how B vs C compare. For **Option C on free tier** (platforms, queue, worker layout), see **[Option C free tier](OPTION_C_FREE_TIER.md)**.

Pick one (or mix elements) and we can implement in that direction.

---

## 1. Current picture

- **Gateway** (8000): health, explain, chat WS, narrator WS; proxies REST to graph / RAG / program.
- **Graph** (8001): analysis, file tree, graph persistence — **CPU-heavy** (parsing, graph build).
- **RAG** (8003): keyword + vector search — **CPU + optional GPU** (embeddings, Milvus).
- **Program** (8004): program graphs, summarization, code gen — **LLM-heavy** (Ollama/OpenAI/Anthropic).
- **Chat** today: built-in Ollama in the gateway → can feel “robotic”; you want to **send already-processed or natural conversation** from another service.

---

## 2. Option A — Docker everywhere, one external “conversation” API

**Idea:** Keep current protocols (JSON over HTTP/WS). Run everything in **Docker**; deploy to **one or two platforms** (e.g. same cloud, different regions). Offload only **chat** to an external open-source stack so it feels less robotic.

| What | How |
|------|-----|
| **Containers** | Use existing `docker-compose.yml` + `docker-compose.microservices.yml`. All services (gateway, graph, rag, program) in containers; Postgres, Mongo, Milvus in containers. |
| **Deploy** | Same cloud (e.g. Azure Container Apps, AWS ECS, or Fly.io): one “app” per service; or one K8s cluster with one deployment per service. Graph/Program can use larger CPU/memory. |
| **Conversation** | Add config: `EZDOCS_CONVERSATION_API_URL` (and optional API key). Gateway’s `/ws/chat`: instead of calling Ollama directly, **POST** the message history to this URL (streaming response). That URL is your **open-source “conversation” service** (e.g. **Open WebUI** backend, **LiteLLM** proxy, **LocalAI**, or a small **FastAPI + vLLM/LM Studio**). You run a model there that’s “more natural”; EzDocs just forwards. |
| **Protocols** | No change: JSON for requests/responses, plain text or JSON for WS chat. |

**Pros:** Small change set; Docker and multi-platform deploy are straightforward; you choose the model and stack for “natural” chat.  
**Cons:** Still JSON everywhere; no per-service protocol optimization.

---

## 3. Option B — Protocol-per-service + conversation service

**Idea:** Optimize **wire format per service**; keep Docker and add a **dedicated conversation service** (open-source) that returns “already processed” / natural replies.

| Service | Protocol | Reason |
|---------|----------|--------|
| **Graph** | **MessagePack** (or keep JSON with gzip) | Large payloads (nodes/edges); MessagePack smaller and faster to parse. |
| **RAG** | **gRPC** (or REST + MessagePack) | Small request (query + codebase_id), response = list of chunks; gRPC gives typed, low-latency. |
| **Program** | **REST + MessagePack** for `/generated` and list responses | Smaller payloads when returning big code blobs. |
| **Chat / conversation** | **WebSocket + binary frames** (MessagePack or raw chunks) | Stream tokens as binary; less overhead than JSON per chunk. |
| **Gateway ↔ services** | Either **HTTP + MessagePack** or **gRPC** for graph/RAG | Gateway encodes/decodes; frontend still gets JSON or SSE if we want to keep the frontend simple. |

**Conversation:** Same as A: gateway sends conversation (or last N turns) to an **external conversation API** (Open WebUI, LiteLLM, LocalAI, custom FastAPI). That service returns a **stream**; gateway forwards to the client (binary or JSON, your choice).

**Containers / deploy:** Same Docker setup; deploy graph on “high CPU”, RAG (and optional embedding) on “GPU” if you add GPU nodes, program and gateway on standard. Each service can live on a **different platform** (e.g. graph on Fly.io, RAG on Azure Container Apps with GPU, program on AWS Lambda or ECS).

**Pros:** Best fit per service (size, latency, streaming); natural chat from your chosen stack.  
**Cons:** More implementation work (MessagePack/gRPC clients and servers); frontend may still speak JSON to gateway.

---

## 4. Option C — Event-driven workers + external conversation

**Idea:** Heavy work (graph build, RAG index, code gen) runs in **async workers** that consume from a **queue**; gateway stays synchronous for “quick” reads and for streaming chat. Conversation comes from an **external open-source service**.

| What | How |
|------|-----|
| **Containers** | Docker for gateway, workers, and queues (Redis/RabbitMQ/Kafka). Each worker type (graph, rag, program) can scale independently. |
| **Flow** | User triggers “analyze” → gateway enqueues job → returns job_id; frontend polls or uses WS for status; worker processes and writes result to Postgres/cache; “get graph” stays HTTP. Same pattern for “index RAG” and “generate code”. |
| **Conversation** | Gateway does **not** run Ollama for chat. It forwards the conversation to an **external conversation API** (streaming); that API is your open-source stack (e.g. Open WebUI, LiteLLM, LocalAI) with a natural model. |
| **Protocols** | Queue messages: **MessagePack** or JSON. Frontend ↔ gateway: keep JSON/WS for simplicity; gateway ↔ conversation API: whatever that API speaks (usually JSON/SSE). |

**Deploy:** Gateway on a small instance/edge; workers on **different platforms** by type (e.g. graph workers on big CPU VMs, RAG workers near Milvus, program workers near MongoDB). Good when you want to scale each workload independently.

**Pros:** Decouples “trigger” from “run”; easy to scale and to put each worker type on the right platform.  
**Cons:** More moving parts (queues, job store, polling or WS for status); latency for “analyze” is higher until job completes.

---

## 5. Option D — “Conversation proxy” in front of any model

**Idea:** Focus on **conversation quality** and **containers** first; leave protocols as-is. Add a **conversation proxy** that can sit in front of Ollama or any open-source API and “smooth” or rewrite replies (e.g. tone, structure).

| What | How |
|------|-----|
| **Containers** | Same Docker Compose; deploy services on 1–2 platforms. |
| **Conversation** | New **small service** (or config to an external one): “conversation proxy”. Gateway sends **already built** message history to it; it can (1) call your **open-source LLM** (LocalAI, Open WebUI, etc.) and return the stream, or (2) call built-in Ollama, then pass the reply through a **second “naturalizer”** model or rule set and return that. So you get “processed conversation” from one place. |
| **Protocols** | JSON/WS as now; optional: proxy returns **Server-Sent Events** or **binary stream** to gateway to save a bit of overhead. |

**Deploy:** Run the proxy in its own container; can be on the same host as gateway or on a different platform (e.g. GPU node for the naturalizer model).

**Pros:** Clear separation: “conversation” is always from one configurable place; you can swap Ollama for a better model or add a naturalizer without touching the rest of EzDocs.  
**Cons:** One more service; protocols unchanged.

---

## 6. Option E — Multi-platform + minimal protocol change + external chat only

**Idea:** Maximize **deploy flexibility** and **conversation quality** with **minimal** protocol work.

| What | How |
|------|-----|
| **Containers** | Docker for all services; **each service can be deployed on a different platform** (e.g. gateway on Cloudflare Workers or a small VM; graph on Azure Container Apps; RAG on AWS; program on Fly.io). Gateway only needs to know service URLs (env vars). |
| **Conversation** | **Only** chat uses an external service. Gateway’s `/ws/chat` calls `EZDOCS_CONVERSATION_API_URL` (streaming); built-in Ollama remains for **explain** and **narrator**. So “already processed” / natural conversation comes only from that URL; no change to explain/narrator. |
| **Protocols** | Keep JSON everywhere **except** optionally: **chat stream** from external API can be **binary** or **SSE**; gateway adapts and forwards to the client (still WS). No change to graph/RAG/program APIs. |

**Pros:** Easiest path to “different platforms per service” and “less robotic chat” with minimal code; protocol change only at the chat boundary if you want.  
**Cons:** No broad protocol optimization; only chat gets the “different protocol” treatment if you add it.

---

## 7. Summary table

| Option | Containers | Deploy | Conversation | Protocols |
|--------|------------|--------|--------------|-----------|
| **A** | Docker, existing compose | 1–2 platforms | External API URL for chat | JSON everywhere |
| **B** | Docker | Different platforms per service | External conversation API | MessagePack/gRPC per service, binary WS for chat |
| **C** | Docker + queues | Workers on different platforms | External conversation API | JSON/MessagePack in queues; JSON at edge |
| **D** | Docker | 1–2 platforms | “Conversation proxy” (rewrite or open-source LLM) | JSON; optional SSE/binary from proxy |
| **E** | Docker | Different platform per service | External URL for chat only; explain/narrator stay Ollama | JSON; optional binary/SSE for chat stream only |

---

## 8. Suggested “first step” no matter which option

1. **Containers:** Harden the existing Docker Compose (resource limits, health checks, env-based URLs) so the same stack runs the same way locally and in the cloud.
2. **Conversation:** Add **one** config: `EZDOCS_CONVERSATION_API_URL` (and optional key). When set, gateway’s chat uses that URL (streaming) instead of built-in Ollama. You can point it at Open WebUI, LiteLLM, LocalAI, or your own FastAPI. That gives “already processed / less robotic” conversation without locking you into one option.
3. **Protocols:** If you choose B or E, we can introduce MessagePack or gRPC for the services you care about most (e.g. graph response, RAG query, or chat stream) step by step.

Tell me which option (or mix) you prefer (e.g. “A for deploy, D for conversation” or “B with E’s chat-only protocol”), and we can break it into concrete tasks and implement.

---

## 9. Free hosting (e.g. Vercel) — where each part can run

**Assumption:** You want to use **free hosting** like **Vercel** where possible. That changes where the frontend vs backend vs conversation service should live.

### 9.1 What fits Vercel (free tier)

| Part | On Vercel? | Notes |
|------|------------|--------|
| **Frontend (React/Vite)** | ✅ **Yes** | Static export or SSR; ideal. Point domain, deploy from Git; free tier is generous. |
| **Serverless API routes** | ⚠️ **Limited** | 10s timeout (hobby) / 60s (Pro); no long-lived WebSockets; cold starts. OK for: health, simple REST proxy, auth callbacks. |
| **Gateway (full API + WS)** | ❌ **No** | WebSockets and long-running requests (explain, chat stream, narrator) exceed serverless limits. |
| **Graph / RAG / Program services** | ❌ **No** | CPU/long-running; need a real process or container. |
| **Conversation service (your LLM)** | ❌ **No** | If self-hosted (Ollama, Open WebUI, etc.) it needs a long-running server. If you use a **hosted API** (e.g. Groq free tier, OpenAI compatible), then a **thin Vercel serverless proxy** could forward chat there and stream back, within timeout limits. |

So: **frontend on Vercel** is the natural fit. **Backend (gateway + services)** and any **self-hosted conversation** service need to run **off Vercel**.

### 9.2 Where to run the backend (free / cheap)

Use **one** of these for the API (monolith or gateway + services), so the frontend (`VITE_API_URL`) points here:

| Platform | Free tier | Good for | Limits |
|----------|-----------|----------|--------|
| **Railway** | Yes (usage-based credit) | Single backend app or gateway + 1–2 services; Docker or Nixpacks. | Credit caps; sleep after inactivity on free. |
| **Render** | Yes | Web service (Docker or native); can run gateway or monolith. | Spins down after ~15 min idle; cold start on next request. |
| **Fly.io** | Yes | Multiple small VMs (one per service); global regions. | 3 shared-cpu VMs, 256MB each; no persistent local disk. |
| **Single VPS** (e.g. Oracle Free Tier, free tier EC2) | Yes | One machine: monolith or Docker Compose (gateway + graph + rag + program). | Usually 1 small instance; you manage OS. |

**Databases:** Vercel doesn’t run Postgres/Mongo/Milvus. Use **free managed DBs** elsewhere and give the backend their URLs:

- **Postgres:** Neon, Supabase, or Railway (free tier).
- **MongoDB:** MongoDB Atlas (free M0).
- **Milvus:** Run on the same host as the backend (Docker) or use a free-tier vector DB (e.g. Upstash Vector, or skip vector and use only Postgres keyword RAG on free tier).

### 9.3 Conversation service on free hosting

- **Option 1 — Hosted LLM API (e.g. Groq, OpenAI-compatible free tier):** No extra server. Gateway calls that API from **Railway/Render/Fly** (your backend). No Vercel involved for chat.
- **Option 2 — Self-hosted open-source (Open WebUI, LiteLLM, LocalAI):** Run in a **container on Railway/Render/Fly** or on the same VPS as the backend. Not on Vercel.
- **Option 3 — Thin proxy on Vercel:** Only if the **external API** responds quickly and you can stream within ~60s. Vercel serverless can proxy request → external conversation API and stream response. Risky for long chats; better to call the conversation API **from your backend** (Railway/Render/Fly).

### 9.4 Recommended split when using Vercel (free)

| Component | Host | Notes |
|------------|------|--------|
| **Frontend** | **Vercel** | Build: `npm run build`; output: `dist/` (or Vite static). Set `VITE_API_URL` to your backend URL. |
| **Backend (gateway or monolith)** | **Railway / Render / Fly.io** (one of them) | Single deploy: either full monolith or gateway + 3 services (graph, rag, program). All share same Postgres/Mongo (and optional Milvus). |
| **Postgres** | **Neon / Supabase / Railway** (free) | `DATABASE_URL` in backend env. |
| **MongoDB** | **Atlas** (free M0) | `MONGODB_URI` in backend env. |
| **Conversation (less robotic chat)** | **Same backend** calling **external API** (e.g. Groq, or Open WebUI on same Railway/Render/Fly stack) | Set `EZDOCS_CONVERSATION_API_URL`; no need for Vercel. |

So: **Vercel = frontend only**; **one free-tier app (Railway/Render/Fly) = backend + optional conversation container**; **DBs = free managed services**. Containers (Docker) are still useful for that backend app and for local dev; you don’t need to split into many platforms if you’re on free tiers.

### 9.5 How each option (A–E) changes with Vercel + free backend

- **A, D, E:** Backend (gateway or monolith) + optional conversation URL run on **one** free-tier host (Railway/Render/Fly). Frontend on Vercel. No change to protocols; keep JSON. Easiest.
- **B:** Same as above for *where* to run; protocol optimizations (MessagePack/gRPC) still apply between gateway and services if you run multiple services on that same host or split across two free-tier apps.
- **C:** Queues + workers need a place to run (same host or second free-tier app). **Feasible on free tier** if you use **Upstash Redis** (free) for the queue and run **gateway + worker in one app** on Railway/Render/Fly. See **[Option C free tier](OPTION_C_FREE_TIER.md)** for platform-by-platform feasibility.

**Bottom line:** With **Vercel for frontend** and **free-tier backend** elsewhere, prefer **Option A or E**: frontend on Vercel, single backend (monolith or gateway + services) on Railway/Render/Fly, conversation via `EZDOCS_CONVERSATION_API_URL` to a hosted or self-hosted API. Add protocol optimizations (B) or conversation proxy (D) later if needed.

---

## 10. B vs C — and what runs on Vercel / Netlify

You're using **Vercel or Netlify**. Here's what can actually run there, then a direct **B vs C** comparison.

### 10.1 What is deployable on Vercel / Netlify

Both platforms are built for **frontend + serverless functions**, not long-running backends.

| Deployable on Vercel / Netlify | Not deployable there |
|-------------------------------|------------------------|
| **Frontend (React/Vite)** — static or SSR. Build to `dist/`; connect to backend via `VITE_API_URL`. | **Full API gateway** — needs WebSockets (chat, narrator) and long request times (e.g. >10–60s). |
| **Optional:** Small **serverless functions** (e.g. rewrite/proxy to backend, auth callback, health). Must finish within ~10s (Vercel hobby) / 26s (Netlify); no long-lived WebSockets. | **Graph / RAG / Program services** — CPU- or LLM-heavy; need a real process or container. |
| **Optional:** Edge functions (Vercel) for very fast, short logic (e.g. redirect). | **Queues, workers, Redis** — no long-running consumers on serverless. |
| **Conversation:** Only as a **thin proxy** to an external API if the call + stream fits in the function timeout. Risky for long chats; not recommended. | **Ollama / self-hosted LLM** — long-running; must run elsewhere. |

So "everything deployable on them" means:

- **On Vercel/Netlify:** Frontend + (optional) tiny serverless helpers. The frontend calls a **backend URL** (hosted elsewhere).
- **Not on Vercel/Netlify:** Backend (gateway, graph, RAG, program, conversation service, queues, workers). These run on **Railway, Render, Fly.io**, or a VPS.

Your **backend URL** (e.g. `https://your-app.railway.app`) is set in the frontend as `VITE_API_URL`. Both B and C keep this split: **frontend on Vercel/Netlify, backend elsewhere**.

### 10.2 Option B — Protocol-per-service (optimized wire format)

**What it is:** Each backend service uses a protocol chosen for its use case (MessagePack for large payloads, gRPC for RAG, binary WebSocket for chat). The architecture is still: request → gateway → service → response. No queues; synchronous request/response (or streaming).

**Pros**

- **Fits Vercel/Netlify:** Frontend stays on Vercel/Netlify; only the backend (on Railway/Render/Fly) speaks MessagePack/gRPC/binary. No serverless limits touched.
- **Better performance:** Smaller payloads (graph), lower latency (RAG), less overhead on chat stream.
- **Same mental model:** Still "call API → get result"; no jobs, no polling.
- **One backend host is enough:** Gateway + graph + RAG + program can all live on one Railway/Render/Fly app. No extra infra.
- **Scales with your backend:** When you upgrade the backend, the same protocols still apply.

**Cons**

- **More implementation work:** MessagePack and/or gRPC on backend and gateway; frontend can keep using JSON to the gateway.
- **Debugging:** Binary protocols need the right tools; slightly harder than "curl + JSON".
- **Backend only:** All protocol changes are on the backend host; Vercel/Netlify only serve the frontend.

**Deployability:** Fully compatible with Vercel/Netlify. Frontend on Vercel or Netlify; backend (with B's protocol choices) on Railway, Render, or Fly.io.

### 10.3 Option C — Event-driven workers + queues

**What it is:** User actions (e.g. "analyze", "index RAG", "generate code") create **jobs** in a queue. A **worker** (separate process) picks up the job, does the heavy work, and writes the result to DB or cache. The frontend **polls** or uses **WebSocket** to ask "is my job done?" and then fetches the result. Chat stays synchronous (gateway → conversation API → stream back).

**Pros**

- **Decoupled:** Gateway stays light; heavy work runs in workers. You can scale workers independently.
- **Resilient:** If a worker dies, the job can be retried from the queue.
- **Flexible placement:** Workers can run on a different free-tier app or a bigger machine.
- **Fits "run on different platforms":** Gateway on one host, graph worker on another, etc.

**Cons**

- **Does not run on Vercel/Netlify:** Queues and workers need a **long-running process**. That means Railway/Render/Fly or a VPS — not serverless.
- **More moving parts:** Queue (Redis/RabbitMQ/Upstash), job store, worker process(es), and status API or WS.
- **Higher latency for heavy ops:** User waits for "job done" (polling or WS) instead of "response ready" in one request.
- **Free-tier cost:** You need a queue (e.g. Upstash Redis free) and at least one worker process; that's an extra app or the same app running a worker loop.
- **Frontend changes:** "Analyze" becomes "submit job → poll/WS for status → fetch graph when done". More UI logic.

**Deployability:** Still **frontend on Vercel/Netlify**. Gateway, queue, and workers all run on Railway/Render/Fly or a VPS. So "deployable on Vercel/Netlify" again means: only the **frontend** (and optional tiny serverless) is on Vercel/Netlify; the rest is elsewhere.

### 10.4 B vs C — direct comparison (with Vercel/Netlify in mind)

| Criteria | Option B | Option C |
|----------|----------|----------|
| **Runs on Vercel/Netlify?** | Frontend only. Backend on Railway/Render/Fly. | Frontend only. Gateway + queue + workers off Vercel/Netlify. |
| **Complexity** | Medium (new protocols on backend). No queues, no job status. | Higher (queue, workers, job status API or WS, frontend polling/WS). |
| **Latency (e.g. analyze)** | One request → wait for response (single round trip). | Submit job → poll until done → fetch result. Often feels slower. |
| **Best for** | Optimizing throughput and payload size without changing the "call API, get result" flow. | When you must offload heavy work to separate workers and accept async + polling. |
| **Free-tier friendliness** | One backend app is enough. | Needs queue + at least one worker process. Tighter on free credits. |
| **Conversation (less robotic)** | Same: gateway calls external conversation API. | Same. |

**Summary:** With **Vercel or Netlify** as the frontend host, **Option B** is usually the better fit: you get protocol optimizations (MessagePack, gRPC, binary stream) on the backend without adding queues and workers. **Option C** is better only if you specifically want job-based, async heavy work and are fine running queue + workers **outside** Vercel/Netlify. Both options keep the same rule: **only frontend (and optional tiny serverless) on Vercel/Netlify; everything else runs on a backend host of your choice.**
