"""
MongoDB service for EzDocs — store generated code only. 5MB limit per document.
"""

import json
import logging
from datetime import datetime
from typing import Any, Optional

from shared.config import (
    GENERATED_CODE_MAX_BYTES,
    MONGODB_DB,
    MONGODB_GENERATED_COLLECTION,
    MONGODB_URI,
)

log = logging.getLogger("ezdocs.mongo")

_client: Any = None


def _get_client():
    global _client
    if _client is not None:
        return _client
    if not MONGODB_URI:
        return None
    try:
        from pymongo import MongoClient
        _client = MongoClient(MONGODB_URI)
        _client.admin.command("ping")
        log.info("MongoDB connected")
        return _client
    except Exception as e:
        log.warning("MongoDB connection failed: %s", e)
        return None


def is_available() -> bool:
    return _get_client() is not None


def _artifact_size(artifacts: dict[str, str]) -> int:
    return len(json.dumps(artifacts, ensure_ascii=False).encode("utf-8"))


def save_generated_code(
    user_id: str,
    program_id: str,
    artifacts: dict[str, str],
    *,
    generation_id: Optional[str] = None,
) -> Optional[str]:
    client = _get_client()
    if not client:
        return None
    size = _artifact_size(artifacts)
    if size > GENERATED_CODE_MAX_BYTES:
        log.warning("Generated code exceeds %d bytes: %d", GENERATED_CODE_MAX_BYTES, size)
        return None
    import uuid
    gid = generation_id or str(uuid.uuid4())
    doc = {
        "_id": gid,
        "user_id": user_id,
        "program_id": program_id,
        "artifacts": artifacts,
        "size_bytes": size,
        "created_at": datetime.utcnow(),
    }
    try:
        db = client[MONGODB_DB]
        coll = db[MONGODB_GENERATED_COLLECTION]
        coll.insert_one(doc)
        log.info("Saved generated code %s for user %s (%d bytes)", gid, user_id, size)
        return gid
    except Exception as e:
        log.warning("MongoDB save failed: %s", e)
        return None


def get_generated_code(generation_id: str) -> Optional[dict[str, Any]]:
    client = _get_client()
    if not client:
        return None
    try:
        db = client[MONGODB_DB]
        doc = db[MONGODB_GENERATED_COLLECTION].find_one({"_id": generation_id})
        if not doc:
            return None
        return {
            "user_id": doc["user_id"],
            "program_id": doc["program_id"],
            "artifacts": doc["artifacts"],
            "created_at": doc["created_at"],
        }
    except Exception as e:
        log.warning("MongoDB get failed: %s", e)
        return None


def list_generated_for_user(user_id: str, limit: int = 50) -> list[dict[str, Any]]:
    client = _get_client()
    if not client:
        return []
    try:
        db = client[MONGODB_DB]
        cursor = db[MONGODB_GENERATED_COLLECTION].find(
            {"user_id": user_id},
            {"_id": 1, "program_id": 1, "created_at": 1, "size_bytes": 1},
        ).sort("created_at", -1).limit(limit)
        return [
            {
                "generation_id": doc["_id"],
                "program_id": doc["program_id"],
                "created_at": doc["created_at"],
                "size_bytes": doc.get("size_bytes", 0),
            }
            for doc in cursor
        ]
    except Exception as e:
        log.warning("MongoDB list failed: %s", e)
        return []
