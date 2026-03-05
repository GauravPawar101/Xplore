"""
Milvus service for EzDocs — store and search graph node embeddings (vector RAG).

Collection: one vector per (codebase_id, symbol_id). Optional: filter by codebase_id when searching.
"""

import logging
from typing import Any, Optional
from urllib.parse import urlparse

from shared.config import EMBEDDING_DIM, MILVUS_COLLECTION, MILVUS_URI

log = logging.getLogger("ezdocs.milvus")

_connected = False


def _connect() -> bool:
    global _connected
    if _connected:
        return True
    try:
        from pymilvus import connections, utility
        parsed = urlparse(MILVUS_URI)
        host = parsed.hostname or "localhost"
        port = parsed.port or 19530
        connections.connect("default", host=host, port=port)
        _connected = True
        log.info("Milvus connected to %s:%s", host, port)
        return True
    except Exception as e:
        log.warning("Milvus connection failed: %s", e)
        return False


def is_available() -> bool:
    return _connect()


def _ensure_collection(dim: int = EMBEDDING_DIM) -> Any:
    """Create collection if not exists; return Collection."""
    from pymilvus import Collection, CollectionSchema, DataType, FieldSchema, utility
    _connect()
    if utility.has_collection(MILVUS_COLLECTION):
        return Collection(MILVUS_COLLECTION)
    fields = [
        FieldSchema(name="id", dtype=DataType.VARCHAR, max_length=256, is_primary=True, auto_id=False),
        FieldSchema(name="codebase_id", dtype=DataType.VARCHAR, max_length=256),
        FieldSchema(name="symbol_id", dtype=DataType.VARCHAR, max_length=512),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
    ]
    schema = CollectionSchema(fields, "EzDocs graph node embeddings")
    coll = Collection(MILVUS_COLLECTION, schema)
    coll.create_index("embedding", {"index_type": "IVF_FLAT", "metric_type": "IP", "params": {"nlist": 128}})
    log.info("Created Milvus collection %s dim=%d", MILVUS_COLLECTION, dim)
    return coll


def insert_embeddings(
    codebase_id: str,
    symbol_ids: list[str],
    embeddings: list[list[float]],
    *,
    dim: Optional[int] = None,
) -> bool:
    """Insert or replace vectors for (codebase_id, symbol_id). Len(symbol_ids) == len(embeddings)."""
    if not symbol_ids or len(symbol_ids) != len(embeddings):
        return False
    try:
        from pymilvus import Collection, utility
        _connect()
        dim = dim or EMBEDDING_DIM
        if not utility.has_collection(MILVUS_COLLECTION):
            _ensure_collection(dim)
        coll = Collection(MILVUS_COLLECTION)
        # Build ids: unique per row (we use composite to support upsert-by-delete-then-insert per codebase)
        ids = [f"{codebase_id}::{sid}" for sid in symbol_ids]
        # Delete existing for this codebase to avoid duplicates
        try:
            coll.load()
            coll.delete(expr=f'codebase_id == "{codebase_id.replace(chr(34), chr(92)+chr(34))}"')
            coll.flush()
        except Exception:
            pass
        coll.insert([
            ids,
            [codebase_id] * len(symbol_ids),
            symbol_ids,
            embeddings,
        ])
        coll.flush()
        log.info("Inserted %d embeddings for codebase %s", len(symbol_ids), codebase_id)
        return True
    except Exception as e:
        log.warning("Milvus insert failed: %s", e)
        return False


def search(
    codebase_id: str,
    query_embedding: list[float],
    k: int = 10,
    *,
    dim: Optional[int] = None,
) -> list[dict[str, Any]]:
    """
    Vector search within codebase_id. Returns list of { symbol_id, distance }.
    """
    try:
        from pymilvus import Collection, utility
        _connect()
        dim = dim or len(query_embedding)
        if not utility.has_collection(MILVUS_COLLECTION):
            return []
        coll = Collection(MILVUS_COLLECTION)
        coll.load()
        results = coll.search(
            data=[query_embedding],
            anns_field="embedding",
            param={"metric_type": "IP", "params": {"nprobe": 16}},
            expr=f'codebase_id == "{codebase_id.replace(chr(34), chr(92)+chr(34))}"',
            limit=k,
            output_fields=["symbol_id"],
        )
        out = []
        for hits in results:
            for h in hits:
                out.append({"symbol_id": h.entity.get("symbol_id"), "distance": float(h.distance)})
        return out
    except Exception as e:
        log.warning("Milvus search failed: %s", e)
        return []


def delete_codebase(codebase_id: str) -> bool:
    """Remove all vectors for a codebase."""
    try:
        from pymilvus import Collection, utility
        _connect()
        if not utility.has_collection(MILVUS_COLLECTION):
            return True
        coll = Collection(MILVUS_COLLECTION)
        coll.delete(expr=f'codebase_id == "{codebase_id.replace(chr(34), chr(92)+chr(34))}"')
        coll.flush()
        return True
    except Exception as e:
        log.warning("Milvus delete failed: %s", e)
        return False
