"""Job queue endpoints: submit analyze job, poll status, get result."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException

from shared.config import MAX_FILES_CEILING
from shared.jobqueue import enqueue, get_result, get_status, is_available
from shared.schemas import JobAnalyzeRequest

log = logging.getLogger("ezdocs")

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.post("/analyze")
async def submit_analyze_job(body: JobAnalyzeRequest) -> dict[str, Any]:
    """
    Enqueue a graph analysis job (local path or GitHub url).
    Returns { job_id }. Poll GET /jobs/{job_id}/status then GET /jobs/{job_id}/result.
    """
    if not is_available():
        raise HTTPException(
            status_code=503,
            detail="Job queue not available.",
        )
    if not body.path and not body.url:
        raise HTTPException(status_code=400, detail="Provide path or url")
    raw_max_files = int(body.max_files)
    max_files = 0 if raw_max_files <= 0 else min(raw_max_files, MAX_FILES_CEILING)
    job_id = enqueue(
        "graph_analyze",
        {
            "path": body.path,
            "url": body.url,
            "max_files": max_files,
            "codebase_id": body.codebase_id,
            "user_id": body.user_id,
        },
    )
    if not job_id:
        raise HTTPException(status_code=503, detail="Failed to enqueue job")
    return {"job_id": job_id}


@router.get("/{job_id}/status")
async def job_status(job_id: str) -> dict[str, Any]:
    """Return { status, error?, result? } for the job."""
    data = get_status(job_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return data


@router.get("/{job_id}/result")
async def job_result(job_id: str) -> dict[str, Any]:
    """Return the job result (e.g. graph). 404 if not done or expired."""
    result = get_result(job_id)
    if result is None:
        raise HTTPException(
            status_code=404,
            detail="Result not available (job pending, running, failed, or expired)",
        )
    return result
