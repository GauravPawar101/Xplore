"""Request cancellation and cooperative shutdown helpers for EzDocs services."""

from __future__ import annotations

import threading
import uuid
from collections.abc import Callable

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

REQUEST_ID_HEADER = "x-ezdocs-request-id"


class RequestCancelledError(RuntimeError):
    """Raised when a long-running request is cancelled or service is shutting down."""


_lock = threading.Lock()
_active_requests: set[str] = set()
_cancelled_requests: set[str] = set()
_shutting_down = False


def begin_request(request_id: str) -> None:
    with _lock:
        _active_requests.add(request_id)
        _cancelled_requests.discard(request_id)


def end_request(request_id: str) -> None:
    with _lock:
        _active_requests.discard(request_id)
        _cancelled_requests.discard(request_id)


def cancel_request(request_id: str) -> bool:
    with _lock:
        existed = request_id in _active_requests
        _cancelled_requests.add(request_id)
        return existed


def cancel_all_requests() -> int:
    with _lock:
        _cancelled_requests.update(_active_requests)
        return len(_active_requests)


def set_shutting_down(value: bool) -> None:
    global _shutting_down
    with _lock:
        _shutting_down = value
        if value:
            _cancelled_requests.update(_active_requests)


def is_shutting_down() -> bool:
    with _lock:
        return _shutting_down


def is_request_cancelled(request_id: str | None = None) -> bool:
    with _lock:
        return _shutting_down or bool(request_id and request_id in _cancelled_requests)


def raise_if_cancelled(request_id: str | None = None) -> None:
    if is_request_cancelled(request_id):
        raise RequestCancelledError("Request cancelled or service shutting down")


def request_id_from_headers(headers: dict[str, str] | Request) -> str | None:
    if isinstance(headers, Request):
        return headers.headers.get(REQUEST_ID_HEADER)
    return headers.get(REQUEST_ID_HEADER)


class RequestControlMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        path = request.scope.get("path", "")
        if is_shutting_down() and not path.startswith("/internal/"):
            return Response(status_code=503, content="Service shutting down")

        request_id = request.headers.get(REQUEST_ID_HEADER) or str(uuid.uuid4())
        request.state.request_id = request_id
        begin_request(request_id)
        try:
            response = await call_next(request)
        finally:
            end_request(request_id)

        response.headers.setdefault(REQUEST_ID_HEADER, request_id)
        return response