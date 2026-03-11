"""
EzDocs API Gateway

Single entry point (port 8000). Serves AI + narrator in-process; proxies graph, RAG, and program to microservices.

Run from backend/: uvicorn gateway.app:app --port 8000
Or: python -m gateway
"""

import logging
import uuid
from contextlib import asynccontextmanager
from typing import AsyncIterator, Callable

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from shared import request_control
from shared.config import (
    CORS_ORIGINS,
    CORS_ORIGIN_REGEX,
    API_TITLE,
    API_VERSION,
    PORT,
    WS_MAX_SIZE,
    GRAPH_SVC_URL,
    RAG_SVC_URL,
    PROGRAM_SVC_URL,
    AI_SVC_URL,
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ezdocs.gateway")

PROXY_PREFIXES = [
    ("/jobs",     GRAPH_SVC_URL.rstrip("/")),   # job queue lives in graph service
    ("/analyze",  GRAPH_SVC_URL.rstrip("/")),
    ("/files",    GRAPH_SVC_URL.rstrip("/")),
    ("/graph",    GRAPH_SVC_URL.rstrip("/")),
    ("/analyses", GRAPH_SVC_URL.rstrip("/")),
    ("/rag",      RAG_SVC_URL.rstrip("/")),
    ("/program",  PROGRAM_SVC_URL.rstrip("/")),
    ("/generate", PROGRAM_SVC_URL.rstrip("/")),
    ("/generated",PROGRAM_SVC_URL.rstrip("/")),
]


def _proxy_target(path: str) -> str | None:
    p = path.rstrip("/") or "/"
    for prefix, base_url in PROXY_PREFIXES:
        if p == prefix or p.startswith(prefix + "/"):
            return base_url
    return None


class ProxyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.scope.get("path", "")
        base_url = _proxy_target(path)
        if not base_url:
            return await call_next(request)
        request_id = request.headers.get(request_control.REQUEST_ID_HEADER) or str(uuid.uuid4())
        url = base_url + path
        if request.scope.get("query_string"):
            url += "?" + request.scope["query_string"].decode()
        client: httpx.AsyncClient = request.app.state.http
        headers = {k: v for k, v in request.headers.items() if k.lower() != "host"}
        headers[request_control.REQUEST_ID_HEADER] = request_id
        request.app.state.active_proxy_requests[request_id] = base_url
        try:
            body = await request.body()
        except Exception:
            body = b""
        try:
            resp = await client.request(
                request.method,
                url,
                headers=headers,
                content=body,
            )
        except httpx.RequestError as e:
            log.warning("Proxy error %s %s: %s", request.method, url, e)
            return Response(status_code=503, content=f"Service unavailable: {e}")
        finally:
            request.app.state.active_proxy_requests.pop(request_id, None)
        out_headers = dict(resp.headers)
        out_headers.pop("transfer-encoding", None)
        out_headers.setdefault(request_control.REQUEST_ID_HEADER, request_id)
        return Response(
            content=resp.content,
            status_code=resp.status_code,
            headers=out_headers,
        )


async def _cancel_downstream_requests(client: httpx.AsyncClient, active_proxy_requests: dict[str, str]) -> None:
    for request_id, base_url in list(active_proxy_requests.items()):
        try:
            await client.post(f"{base_url}/internal/cancel/{request_id}")
        except Exception as exc:
            log.debug("Downstream cancel failed for %s via %s: %s", request_id, base_url, exc)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    log.info("EzDocs Gateway starting…")
    async with httpx.AsyncClient(timeout=120.0) as client:
        app.state.http = client
        app.state.active_proxy_requests = {}
        yield
        await _cancel_downstream_requests(client, app.state.active_proxy_requests)
    log.info("EzDocs Gateway shutting down…")


app = FastAPI(
    title=API_TITLE,
    version=API_VERSION,
    description="EzDocs API Gateway – proxies to graph, RAG, program; serves AI and narrator locally.",
    lifespan=lifespan,
)
app.add_middleware(ProxyMiddleware)
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

app.include_router(meta.router)
app.include_router(ai.router)
app.include_router(narrator_ws.router)


if __name__ == "__main__":
    uvicorn.run(
        "gateway.app:app",
        host=HOST,
        port=PORT,
        reload=False,
        log_level="info",
        ws_max_size=WS_MAX_SIZE,
    )