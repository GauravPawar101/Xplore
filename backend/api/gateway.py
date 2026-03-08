"""Vercel entry point — AI, meta, and narrator routes.

On Vercel, path-based routing in vercel.json sends /analyze, /rag, /program, etc.
to their dedicated handlers. This gateway handles everything else:
  /ai/*     — AI explain / chat / stream
  /health   — health check
  /         — API root / docs

NOTE: WebSocket endpoints (/ws/narrate, /ws/narrate/node) are registered but
Vercel serverless does not support WebSockets. They will return a 501 on Vercel.
Run the monolith locally (python main.py) or deploy to Railway for full WS support.

Required env vars in Vercel dashboard:
  CLERK_JWKS_URL
  HUGGINGFACE_HUB_TOKEN (or OPENAI_API_KEY / ANTHROPIC_API_KEY)
  XPLORE_CORS_ORIGINS=https://your-frontend.vercel.app
  XPLORE_GRAPH_SVC_URL=https://your-graph-service.vercel.app   (if graph is separate)
  XPLORE_RAG_SVC_URL=https://your-rag-service.vercel.app
  XPLORE_PROGRAM_SVC_URL=https://your-program-service.vercel.app
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware

from shared.config import (
    CORS_ORIGINS,
    CORS_ORIGIN_REGEX,
    API_TITLE,
    API_VERSION,
)
from routers import ai, meta, narrator_ws

app = FastAPI(title=API_TITLE, version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[] if CORS_ORIGIN_REGEX else CORS_ORIGINS,
    allow_origin_regex=CORS_ORIGIN_REGEX or None,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=1_000)

app.include_router(meta.router)
app.include_router(ai.router)
app.include_router(narrator_ws.router)
