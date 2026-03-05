"""
AI microservice: explain and chat endpoints.

Run from backend/: uvicorn services.ai_svc:app --port 8002
"""

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from routers import ai, meta, narrator_ws

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="EzDocs AI Service", version="0.2.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(ai.router)
app.include_router(narrator_ws.router)
