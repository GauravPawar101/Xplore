"""
Graph microservice: codebase analysis, file tree, graph persistence.

Run from backend/: uvicorn services.graph_svc:app --port 8001
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from shared.request_control import RequestControlMiddleware, cancel_all_requests, set_shutting_down
from routers import graph, meta
from jobs.router import router as jobs_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    set_shutting_down(False)
    yield
    set_shutting_down(True)
    cancel_all_requests()


app = FastAPI(title="EzDocs Graph Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(RequestControlMiddleware)
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
