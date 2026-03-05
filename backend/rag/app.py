"""
RAG microservice: semantic + keyword retrieval over graph embeddings.

Run from backend/: uvicorn rag.app:app --port 8003
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from routers import meta, rag

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="EzDocs RAG Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(rag.router)
