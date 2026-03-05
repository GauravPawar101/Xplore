# Option C on free tier — platforms and feasibility

This doc makes sure **Option C** (event-driven workers + queues) can run well on free tier and spells out **platform feasibility** (Railway, Render, Fly.io, Vercel/Netlify).

---

## 1. What Option C needs (all free-tier friendly)

| Component | Role | Free-tier requirement |
|-----------|------|------------------------|
| **Queue** | Store jobs (analyze, index RAG, generate code); workers consume from it. | Managed Redis with a free tier. **Upstash Redis** (serverless) has a generous free tier and works from any backend. |
| **Gateway** | Accepts requests, enqueues jobs, returns `job_id`; serves status (poll or WS) and chat/explain/narrator. | One long-running process (cannot be Vercel/Netlify serverless for full gateway). |
| **Worker(s)** | Long-running process(es) that pull jobs from the queue, run graph/RAG/program work, write results to DB/cache. | At least one process that can run 24/7 or wake when jobs exist. |
| **Job store / result cache** | Where workers write results; gateway or frontend reads by `job_id`. | Postgres or Redis (e.g. Upstash) or same DB you already use (Neon, Supabase). |
| **Postgres / MongoDB** | Same as today (graph metadata, program graphs, generated code). | Neon, Supabase, Railway, Atlas — all have free tiers. |

So for C on free tier you need: **one queue (Upstash Redis)**, **one place that runs both gateway and worker** (to save on “number of services”), and **existing free DBs**.

---

## 2. Queue: Upstash Redis (free tier)

| Item | Detail |
|------|--------|
| **What** | Serverless Redis; HTTP REST API, so it works from Vercel/Netlify serverless (for enqueue only) and from any backend. |
| **Free tier** | 10,000 commands/day; enough for small/medium use (enqueue, dequeue, set job result, status). |
| **Use for C** | One list or stream per job type (e.g. `jobs:graph`, `jobs:rag`, `jobs:program`); workers BRPOP or XREAD. Store job result in Redis with TTL or in Postgres by `job_id`. |
| **Feasibility for C** | **Yes** — recommended; keeps C runnable on free tier without self-hosting Redis. |

---

## 3. Platform feasibility for Option C (free tier)

### Railway (free tier)

| Item | Feasibility | Notes |
|------|-------------|--------|
| **Gateway + worker in one app** | **Yes** | Single service: FastAPI gateway + background thread or asyncio task that runs a worker loop (poll Upstash for jobs, run graph/RAG/program, write result). One billable unit. |
| **Gateway and worker as separate services** | **Tight** | Two services = 2x usage against free credit. Free tier has a monthly credit cap; both must fit. Possible for light use. |
| **Queue** | **Use Upstash** | Don’t run Redis on Railway on free tier; use Upstash Redis (free). |
| **Postgres / Mongo** | **Yes** | Railway Postgres or external Neon/Supabase + Atlas. |
| **Will everything run good?** | **Yes**, if you **combine gateway + worker in one app** and use Upstash for the queue. Separate worker service is possible but burns credit faster. |

### Render (free tier)

| Item | Feasibility | Notes |
|------|-------------|--------|
| **Gateway + worker in one app** | **Yes** | One Web Service: gateway handles HTTP/WS; same process runs a worker loop in a background thread. When the service is **awake**, it both serves requests and processes jobs. |
| **Spin-down** | **Important** | Free web services spin down after ~15 min idle. After spin-down, **no worker runs** until the next HTTP request wakes the service. So: jobs enqueue immediately; **execution happens when the next request wakes the service**. Use a free cron (e.g. cron-job.org) to hit `/health` every 10 min to keep it awake, or accept “process on wake”. |
| **Queue** | **Use Upstash** | Same as above. |
| **Will everything run good?** | **Yes**, with the caveat: job processing is “on wake” or when you ping the service. For free tier that’s acceptable; make the frontend show “Job queued; we’ll process it when the server is active” or use a cron to reduce spin-down. |

### Fly.io (free tier)

| Item | Feasibility | Notes |
|------|-------------|--------|
| **Gateway and worker** | **Yes** | Free tier: 3 shared-cpu VMs, 256MB RAM each. Run **gateway on one machine** and **worker on another** (or both in one machine if you prefer). |
| **Memory** | **Tight** | 256MB per VM. Graph parsing (tree-sitter, large codebases) can be memory-heavy. Prefer one VM for gateway (light) and one for worker; if the worker OOMs, restrict max files or run a single job at a time. |
| **Queue** | **Use Upstash** | Don’t run Redis on Fly on free tier; use Upstash. |
| **Will everything run good?** | **Yes**, if you **limit concurrency and payload size** (e.g. max files per analysis) so the worker stays under 256MB. |

### Vercel / Netlify (for Option C)

| Item | Feasibility | Notes |
|------|-------------|--------|
| **Frontend** | **Yes** | Deploy frontend as today; it calls your backend URL (Railway/Render/Fly). |
| **Gateway** | **No** | Gateway must enqueue jobs and serve status (and ideally WebSocket for chat). That needs a long-running process — not Vercel/Netlify. |
| **Worker** | **No** | Workers must run in a loop; cannot run on serverless. |
| **Enqueue-only from Vercel** | **Possible but not enough** | You could have a serverless function that pushes to Upstash and returns `job_id`, but **something else** (Railway/Render/Fly) must run the worker and expose status/result. So you still need a backend host; C doesn’t “run on” Vercel/Netlify beyond the frontend. |

---

## 4. Recommended Option C setup for free tier (everything runs good)

1. **Frontend:** Vercel or Netlify (unchanged).
2. **Queue:** **Upstash Redis** (free tier); use it for job queues and optionally for job result cache (with TTL).
3. **Single backend app (gateway + worker):** Deploy **one** app on **Railway** or **Render** or **Fly.io** that:
   - Runs the **gateway** (enqueue jobs, serve `GET /job/:id/status`, `GET /job/:id/result`, chat, explain, narrator).
   - Runs a **worker loop** in the same process (pull from Upstash, run graph/RAG/program, write result to Postgres or Redis).
4. **Databases:** Neon/Supabase (Postgres) + Atlas (MongoDB), same as today; optional Milvus or skip vector RAG on free tier.
5. **Render only:** If you use Render free, add a **free cron** (e.g. cron-job.org) that hits your gateway every 10–15 min so the service doesn’t spin down, and jobs are processed regularly; or accept that jobs run on “next request.”

With this, **Option C is feasible and runs on free tier**: one backend host, one queue (Upstash), same DBs, frontend on Vercel/Netlify. The main tradeoffs are Render’s spin-down (solved by cron or “process on wake”) and Fly’s 256MB (solved by limiting job size/concurrency).
