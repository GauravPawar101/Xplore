"""
EzDocs Backend API

FastAPI backend for code dependency graphs and streaming LLM explanations.
In-process job queue + worker for async codebase analysis.
Run from backend directory: python main.py
"""

import logging
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import uvicorn

from shared.config import (
    CORS_ORIGINS,
    CORS_ORIGIN_REGEX,
    API_DESCRIPTION,
    API_TITLE,
    API_VERSION,
    HOST,
    PORT,
    RELOAD,
    WS_MAX_SIZE,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from shared.jobqueue import is_available as queue_available, pop_job
from jobs.handlers import run_job
from routers import ai, graph, meta, narrator_ws, rag, program
from jobs.router import router as jobs_router

from dotenv import load_dotenv
load_dotenv()

# ─── Windows event loop fix ──────────────────────────────────────────────────
# Python 3.10+ on Windows defaults to ProactorEventLoop which has known
# incompatibilities with asyncpg. Switch to SelectorEventLoop instead.
if sys.platform == "win32":
    import asyncio
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# ─── Logging ─────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ezdocs")

_worker_stop = threading.Event()

_queue_confirmed: bool = False


def _worker_loop() -> None:
    """Background thread: pop jobs from queue and run them."""
    global _queue_confirmed
    poll_interval = 2.0

    # Confirm queue is reachable once at startup; if not, exit worker entirely.
    if not queue_available():
        log.info("Job queue unavailable — worker exiting.")
        return
    _queue_confirmed = True
    log.info("Job worker confirmed queue connection.")

    while not _worker_stop.is_set():
        item = pop_job()
        if item:
            job_id, payload = item
            try:
                run_job(job_id, payload)
            except Exception as e:
                log.exception("Worker job %s error: %s", job_id, e)
        else:
            # No jobs — sleep before next poll, no ping needed
            time.sleep(poll_interval)
    log.info("Job worker stopped.")


# ─── Lifecycle ───────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("EzDocs API starting up…")

    # Initialise the DB pool NOW — inside the running event loop — so asyncpg
    # never inherits a stale or wrong loop reference.
    from shared import db
    await db.get_pool()

    worker = None
    if queue_available():
        worker = threading.Thread(target=_worker_loop, daemon=True)
        worker.start()
        log.info("Job worker started.")

    yield

    # ── Shutdown ──
    _worker_stop.set()
    if worker and worker.is_alive():
        worker.join(timeout=5)

    await db.close_pool()
    log.info("EzDocs API shutting down…")


# ─── App ────────────────────────────────────────────────────────────────────

app = FastAPI(
    title=API_TITLE,
    description=API_DESCRIPTION,
    version=API_VERSION,
    lifespan=lifespan,
)

# CORS: allow any origin in dev (regex); credentials allowed for Bearer auth
_cors_origins = [] if CORS_ORIGIN_REGEX else CORS_ORIGINS
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=CORS_ORIGIN_REGEX if CORS_ORIGIN_REGEX else None,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1_000)


class PreflightCORSMiddleware(BaseHTTPMiddleware):
    """Handle OPTIONS preflight first and always return CORS headers so XHR preflight succeeds."""

    async def dispatch(self, request: Request, call_next) -> Response:
        origin = request.headers.get("origin")
        if request.method == "OPTIONS":
            return Response(
                status_code=204,
                headers={
                    "access-control-allow-origin": origin,
                    "access-control-allow-credentials": "true",
                    "access-control-allow-methods": "GET, POST, PUT, PATCH, DELETE, OPTIONS",
                    "access-control-allow-headers": request.headers.get("access-control-request-headers", "*"),
                    "access-control-max-age": "86400",
                } if origin else {},
            )
        response = await call_next(request)
        if origin:
            response.headers.setdefault("access-control-allow-origin", origin)
            response.headers.setdefault("access-control-allow-credentials", "true")
        return response


app.add_middleware(PreflightCORSMiddleware)

# ─── Routers ─────────────────────────────────────────────────────────────────

app.include_router(meta.router)
app.include_router(jobs_router)
app.include_router(graph.router)
app.include_router(ai.router)
app.include_router(rag.router)
app.include_router(program.router)
app.include_router(narrator_ws.router)


# ─── Entry Point ────────────────────────────────────────────────────────────

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host=HOST,
        port=PORT,
        reload=RELOAD,
        reload_dirs=["."],
        reload_includes=["*.py"],
        reload_excludes=["ingest/*", "temp/*", "*.tmp", "__pycache__/*"],
        log_level="info",
        ws_max_size=WS_MAX_SIZE,
    )