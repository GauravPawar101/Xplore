"""
Graph microservice: codebase analysis, file tree, graph persistence.

Run from backend/: uvicorn services.graph_svc:app --port 8001
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from routers import graph, meta

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="EzDocs Graph Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(graph.router)
