"""Health and meta endpoints."""

from fastapi import APIRouter

from shared import request_control

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict:
    """Liveness/readiness check."""
    return {"status": "ok"}


@router.post("/internal/cancel/{request_id}")
async def cancel_request(request_id: str) -> dict:
    cancelled = request_control.cancel_request(request_id)
    return {"request_id": request_id, "cancelled": cancelled}


@router.post("/internal/cancel-all")
async def cancel_all_requests() -> dict:
    count = request_control.cancel_all_requests()
    return {"cancelled_requests": count}
