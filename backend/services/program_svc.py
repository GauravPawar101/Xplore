"""
Program microservice: program graphs, summarization, code generation.

Run from backend/: uvicorn services.program_svc:app --port 8004
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from shared.config import CORS_ORIGINS
from shared.request_control import RequestControlMiddleware, cancel_all_requests, set_shutting_down
from routers import meta, program

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


app = FastAPI(title="EzDocs Program Service", version="0.2.0", lifespan=lifespan)
app.add_middleware(RequestControlMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(meta.router)
app.include_router(program.router)
