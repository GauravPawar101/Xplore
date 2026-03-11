"""
Graph microservice: codebase analysis, file tree, graph persistence.

Run from backend/: uvicorn graph.app:app --port 8001
"""

import logging
import threading
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from shared.jobqueue import is_available as queue_available, pop_job
from jobs.handlers import run_job
from routers import graph, meta
from jobs.router import router as jobs_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ezdocs.graph")

_worker_stop = threading.Event()


def _worker_loop() -> None:
    """Background thread: pop jobs from queue and run them."""
    if not queue_available():
        log.info("Job queue unavailable — worker exiting.")
        return
    log.info("Job worker started.")
    while not _worker_stop.is_set():
        item = pop_job()
        if item:
            job_id, payload = item
            try:
                run_job(job_id, payload)
            except Exception as e:
                log.exception("Worker job %s error: %s", job_id, e)
        else:
            time.sleep(2.0)
    log.info("Job worker stopped.")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log = logging.getLogger("xplore.graph")
    await db.get_pool()
    worker = threading.Thread(target=_worker_loop, daemon=True)
    worker.start()
    yield
    _worker_stop.set()
    if worker.is_alive():
        worker.join(timeout=5)
    await db.close_pool()


app = FastAPI(title="EzDocs Graph Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(jobs_router)
app.include_router(graph.router)
