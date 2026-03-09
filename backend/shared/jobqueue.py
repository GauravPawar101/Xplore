"""
In-memory job queue.
Enqueue jobs, store status/result; worker thread pops and processes.
"""

import logging
import queue
import threading
import uuid
from typing import Any

log = logging.getLogger("ezdocs")

# ─── Storage ──────────────────────────────────────────────────────────────────

_job_queue: queue.Queue = queue.Queue()
_job_store: dict[str, dict] = {}
_lock = threading.Lock()


# ─── Public API ───────────────────────────────────────────────────────────────

def is_available() -> bool:
    """In-memory queue is always available."""
    return True


def enqueue(job_type: str, payload: dict[str, Any]) -> str | None:
    """Enqueue a job. Returns job_id."""
    job_id = str(uuid.uuid4())
    with _lock:
        _job_store[job_id] = {
            "status": "pending",
            "payload": {"type": job_type, **payload},
        }
    _job_queue.put(job_id)
    return job_id


def get_status(job_id: str) -> dict | None:
    """Return { status, error?, result? } or None if job unknown."""
    with _lock:
        entry = _job_store.get(job_id)
    if entry is None:
        return None
    out: dict[str, Any] = {"status": entry["status"]}
    if entry["status"] == "done":
        out["result"] = entry.get("result")
    elif entry["status"] == "failed":
        out["error"] = entry.get("error", "")
    return out


def get_result(job_id: str) -> Any | None:
    """Return parsed result for a done job, or None."""
    with _lock:
        entry = _job_store.get(job_id)
    if entry and entry["status"] == "done":
        return entry.get("result")
    return None


def set_running(job_id: str) -> None:
    with _lock:
        if job_id in _job_store:
            _job_store[job_id]["status"] = "running"


def set_result(job_id: str, result: Any) -> None:
    with _lock:
        if job_id in _job_store:
            _job_store[job_id]["status"] = "done"
            _job_store[job_id]["result"] = result


def set_failed(job_id: str, error: str) -> None:
    with _lock:
        if job_id in _job_store:
            _job_store[job_id]["status"] = "failed"
            _job_store[job_id]["error"] = error


def pop_job(timeout_sec: float = 0) -> tuple[str, dict] | None:
    """
    Pop one job from the queue. timeout_sec=0 means non-blocking.
    Returns (job_id, payload_dict) or None.
    """
    try:
        if timeout_sec > 0:
            job_id = _job_queue.get(timeout=timeout_sec)
        else:
            job_id = _job_queue.get_nowait()
    except queue.Empty:
        return None
    with _lock:
        entry = _job_store.get(job_id)
    if entry is None:
        return None
    return (job_id, entry["payload"])
