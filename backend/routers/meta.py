"""Health and meta endpoints."""

from fastapi import APIRouter

router = APIRouter(tags=["meta"])


@router.get("/health")
async def health() -> dict:
    """Liveness/readiness check."""
    return {"status": "ok"}
