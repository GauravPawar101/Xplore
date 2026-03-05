"""
Job queue using Upstash Redis (Option C).
Enqueue jobs, store status/result; worker pops and processes.
"""

import json
import logging
import uuid
from typing import Any

from shared.config import JOB_RESULT_TTL_SECONDS, UPSTASH_REDIS_REST_TOKEN, UPSTASH_REDIS_REST_URL

log = logging.getLogger("ezdocs")

# Queue list key; status/result keys
QUEUE_LIST = "ezdocs:jobs"
JOB_STATUS_PREFIX = "ezdocs:job:"
JOB_PAYLOAD_SUFFIX = ":payload"
JOB_RESULT_SUFFIX = ":result"
JOB_STATUS_SUFFIX = ":status"

_redis = None


def _get_redis():
    """Lazy init Upstash Redis (sync client for worker and simple get/set)."""
    global _redis
    if _redis is not None:
        return _redis
    if not UPSTASH_REDIS_REST_URL or not UPSTASH_REDIS_REST_TOKEN:
        return None
    try:
        from upstash_redis import Redis

        _redis = Redis(url=UPSTASH_REDIS_REST_URL, token=UPSTASH_REDIS_REST_TOKEN)
        return _redis
    except Exception as e:
        log.warning("Upstash Redis not available: %s", e)
        return None


def is_available() -> bool:
    """Return True if queue is configured and reachable."""
    r = _get_redis()
    if r is None:
        return False
    try:
        r.ping()
        return True
    except Exception:
        return False


def enqueue(job_type: str, payload: dict[str, Any]) -> str | None:
    """
    Enqueue a job. Returns job_id or None if queue unavailable.
    payload is stored as JSON; worker will receive it.
    """
    r = _get_redis()
    if r is None:
        return None
    job_id = str(uuid.uuid4())
    key_status = f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}"
    key_payload = f"{JOB_STATUS_PREFIX}{job_id}{JOB_PAYLOAD_SUFFIX}"
    try:
        r.set(key_status, "pending")
        r.set(key_payload, json.dumps({"type": job_type, **payload}))
        r.lpush(QUEUE_LIST, job_id)
        return job_id
    except Exception as e:
        log.exception("Enqueue failed: %s", e)
        return None


def get_status(job_id: str) -> dict | None:
    """Return { status, error?, result? } or None if job unknown."""
    r = _get_redis()
    if r is None:
        return None
    key_status = f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}"
    key_result = f"{JOB_STATUS_PREFIX}{job_id}{JOB_RESULT_SUFFIX}"
    try:
        status = r.get(key_status)
        if status is None:
            return None
        out = {"status": status}
        if status == "done":
            raw = r.get(key_result)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    out["result"] = json.loads(raw)
                except Exception:
                    out["result"] = raw
        elif status == "failed":
            raw = r.get(key_result)
            if raw:
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8")
                try:
                    out["error"] = json.loads(raw).get("error", raw)
                except Exception:
                    out["error"] = raw
        return out
    except Exception as e:
        log.warning("get_status failed for %s: %s", job_id, e)
        return None


def get_result(job_id: str) -> Any | None:
    """Return parsed result for a done job, or None."""
    r = _get_redis()
    if r is None:
        return None
    key_status = f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}"
    key_result = f"{JOB_STATUS_PREFIX}{job_id}{JOB_RESULT_SUFFIX}"
    try:
        if r.get(key_status) != "done":
            return None
        raw = r.get(key_result)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        return json.loads(raw)
    except Exception:
        return None


def set_running(job_id: str) -> None:
    r = _get_redis()
    if r:
        r.set(f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}", "running")


def set_result(job_id: str, result: Any) -> None:
    r = _get_redis()
    if r:
        key_status = f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}"
        key_result = f"{JOB_STATUS_PREFIX}{job_id}{JOB_RESULT_SUFFIX}"
        r.set(key_status, "done")
        r.setex(key_result, JOB_RESULT_TTL_SECONDS, json.dumps(result))


def set_failed(job_id: str, error: str) -> None:
    r = _get_redis()
    if r:
        key_status = f"{JOB_STATUS_PREFIX}{job_id}{JOB_STATUS_SUFFIX}"
        key_result = f"{JOB_STATUS_PREFIX}{job_id}{JOB_RESULT_SUFFIX}"
        r.set(key_status, "failed")
        r.setex(key_result, JOB_RESULT_TTL_SECONDS, json.dumps({"error": error}))


def pop_job(timeout_sec: float = 0) -> tuple[str, dict] | None:
    """
    Pop one job from the queue. timeout_sec=0 means non-blocking (rpop).
    Returns (job_id, payload_dict) or None.
    """
    r = _get_redis()
    if r is None:
        return None
    try:
        raw = r.rpop(QUEUE_LIST)
        if raw is None:
            return None
        job_id = raw if isinstance(raw, str) else raw.decode("utf-8")
        key_payload = f"{JOB_STATUS_PREFIX}{job_id}{JOB_PAYLOAD_SUFFIX}"
        payload_str = r.get(key_payload)
        if not payload_str:
            return None
        if isinstance(payload_str, bytes):
            payload_str = payload_str.decode("utf-8")
        payload = json.loads(payload_str)
        return (job_id, payload)
    except Exception as e:
        log.warning("pop_job failed: %s", e)
        return None
