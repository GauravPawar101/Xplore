"""
Job handlers for the in-process job queue.
Run in worker thread: pop job, dispatch by type, set result/failed.

Job types:
  graph_analyze — parse codebase, persist to Postgres, auto-enqueue graph_explain
  graph_explain — batch-generate explanations for user-code nodes
"""

import asyncio
import logging
from pathlib import Path

from shared.config import MAX_FILES_CEILING, DATABASE_URL
from shared.jobqueue import set_failed, set_result, set_running, enqueue
from routers.graph import build_graph_for
from shared import ingest

log = logging.getLogger("ezdocs")

_EXPLAIN_BATCH_SIZE = 20
_EXPLAIN_MAX_CODE   = 1200


# ─── Async helper (own loop — never touches shared uvicorn pool) ──────────────

def _run_async(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ─── Postgres helpers ─────────────────────────────────────────────────────────

def _persist_graph(codebase_id: str, nodes: list, edges: list, user_id: str, source_path: str) -> None:
    """Bulk-write graph to Postgres via COPY."""
    if not DATABASE_URL or "postgresql" not in DATABASE_URL:
        log.warning("Postgres unavailable; graph for %s not persisted.", codebase_id)
        return

    async def _write():
        import asyncpg
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            async with conn.transaction():
                await conn.execute("DELETE FROM graph_edges WHERE codebase_id = $1", codebase_id)
                await conn.execute("DELETE FROM graph_nodes WHERE codebase_id = $1", codebase_id)

                node_records = []
                for n in nodes:
                    nid  = n.get("id", "")
                    data = n.get("data", {})
                    node_records.append((
                        codebase_id,
                        nid,
                        data.get("label", nid),
                        data.get("type", "function"),
                        data.get("filepath", ""),
                        int(data.get("start_line") or 0),
                        int(data.get("end_line") or 0),
                        (data.get("code") or "")[:10000],
                        # library blobs already have their explanation set by builder
                        (data.get("explanation") or data.get("summary") or "")[:15000],
                    ))
                await conn.copy_records_to_table(
                    "graph_nodes",
                    records=node_records,
                    columns=["codebase_id", "node_id", "name", "type", "filepath",
                             "start_line", "end_line", "code", "summary"],
                )

                seen: set = set()
                edge_records = []
                for e in edges:
                    key = (e.get("source", ""), e.get("target", ""))
                    if key not in seen:
                        seen.add(key)
                        edge_records.append((codebase_id, key[0], key[1], e.get("label", "CALLS")))
                await conn.copy_records_to_table(
                    "graph_edges",
                    records=edge_records,
                    columns=["codebase_id", "source_id", "target_id", "edge_type"],
                )

                if user_id and source_path:
                    await conn.execute(
                        """
                        INSERT INTO analyses (user_id, codebase_id, source_path)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (codebase_id) DO UPDATE SET
                            source_path = EXCLUDED.source_path, user_id = EXCLUDED.user_id
                        """,
                        user_id, codebase_id, source_path,
                    )

            log.info("Persisted graph %s: %d nodes, %d edges", codebase_id, len(nodes), len(edges))
        finally:
            await conn.close()

    try:
        _run_async(_write())
    except Exception as e:
        log.warning("Postgres persist skipped for %s: %s", codebase_id, e)


def _update_node_summary(codebase_id: str, node_id: str, summary: str) -> None:
    if not DATABASE_URL or "postgresql" not in DATABASE_URL:
        return

    async def _write():
        import asyncpg
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            await conn.execute(
                "UPDATE graph_nodes SET summary = $1 WHERE codebase_id = $2 AND node_id = $3",
                summary[:15000], codebase_id, node_id,
            )
        finally:
            await conn.close()

    try:
        _run_async(_write())
    except Exception as e:
        log.warning("Summary update failed for %s/%s: %s", codebase_id, node_id, e)


# ─── graph_analyze ────────────────────────────────────────────────────────────

def _run_graph_analyze(payload: dict) -> dict:
    path        = payload.get("path")
    url         = payload.get("url")
    max_files   = min(int(payload.get("max_files", 200)), MAX_FILES_CEILING)
    codebase_id = payload.get("codebase_id")
    user_id     = payload.get("user_id") or ""

    if url:
        source_path = ingest.clone_github_repo(url)
    elif path:
        source_path = str(_resolve_path(path))
    else:
        raise ValueError("Either path or url is required")

    result = build_graph_for(
        source_path, max_files=max_files,
        codebase_id=codebase_id, user_id=user_id or None,
    )

    if codebase_id:
        _persist_graph(codebase_id, result["nodes"], result["edges"], user_id, source_path)

        # Auto-enqueue background explanation job
        try:
            import json as _json
            # Strip code from nodes before queuing — only need id, label, filepath, type
            # (code is already persisted in Postgres)
            slim_nodes = [
                {
                    "id":   n.get("id", ""),
                    "type": n.get("type", "default"),
                    "data": {
                        "label":      (n.get("data") or {}).get("label", ""),
                        "type":       (n.get("data") or {}).get("type", "function"),
                        "filepath":   (n.get("data") or {}).get("filepath", ""),
                        "code":       (n.get("data") or {}).get("code", ""),
                        "explanation":(n.get("data") or {}).get("explanation", ""),
                    },
                }
                for n in result["nodes"]
            ]
            explain_job_id = enqueue("graph_explain", {
                "codebase_id": codebase_id,
                "nodes":       slim_nodes,
            })
            log.info("Enqueued explanation job %s for codebase %s", explain_job_id, codebase_id)
        except Exception as e:
            log.warning("Could not enqueue explanation job: %s", e)

    return {
        "codebase_id": codebase_id,
        "node_count":  len(result.get("nodes", [])),
        "edge_count":  len(result.get("edges", [])),
        "source_path": source_path,
    }


# ─── graph_explain ────────────────────────────────────────────────────────────

def _run_graph_explain(payload: dict) -> dict:
    """
    Batch-generate summaries for user-code nodes that have code but no explanation.
    Library blob nodes are skipped — they already have fixed blurbs from GraphBuilder.
    Each summary is written back to graph_nodes.summary as it completes.
    """
    codebase_id = payload.get("codebase_id")
    if not codebase_id:
        raise ValueError("codebase_id required for graph_explain")

    nodes: list[dict] = payload.get("nodes") or []

    # Only explain user-code nodes that have code and no explanation yet
    explain_nodes = [
        n for n in nodes
        if n.get("type") != "library"
        and (n.get("data") or {}).get("code", "").strip()
        and not (n.get("data") or {}).get("explanation", "").strip()
    ]

    log.info("graph_explain: %d nodes to explain for codebase %s", len(explain_nodes), codebase_id)

    from shared.ai import generate_summary, AIProviderError

    done = failed = 0
    for i in range(0, len(explain_nodes), _EXPLAIN_BATCH_SIZE):
        batch = explain_nodes[i: i + _EXPLAIN_BATCH_SIZE]
        for node in batch:
            nid  = node["id"]
            data = node.get("data", {})
            code = (data.get("code") or "")[:_EXPLAIN_MAX_CODE].strip()
            name = data.get("label", nid)
            fp   = data.get("filepath", "")
            try:
                summary = generate_summary(code, [], fp)
                _update_node_summary(codebase_id, nid, summary)
                done += 1
            except AIProviderError as e:
                log.warning("Explain failed for %s: %s", name, e)
                failed += 1
            except Exception as e:
                log.warning("Unexpected explain error for %s: %s", name, e)
                failed += 1

        log.info("Explanations progress: %d done, %d failed (batch %d/%d)",
                 done, failed,
                 i // _EXPLAIN_BATCH_SIZE + 1,
                 (len(explain_nodes) + _EXPLAIN_BATCH_SIZE - 1) // _EXPLAIN_BATCH_SIZE)

    return {"codebase_id": codebase_id, "explained": done, "failed": failed}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _resolve_path(raw: str) -> Path:
    p = Path(raw).resolve()
    if not p.exists():
        raise ValueError(f"Path not found: {p}")
    if not p.is_dir():
        raise ValueError(f"Not a directory: {p}")
    return p


# ─── Dispatcher ───────────────────────────────────────────────────────────────

def run_job(job_id: str, payload: dict) -> None:
    set_running(job_id)
    job_type = payload.get("type", "")
    try:
        if job_type == "graph_analyze":
            result = _run_graph_analyze(payload)
            set_result(job_id, result)
        elif job_type == "graph_explain":
            result = _run_graph_explain(payload)
            set_result(job_id, result)
            log.info("Explanation job %s complete: %s", job_id, result)
        else:
            set_failed(job_id, f"Unknown job type: {job_type}")
    except Exception as e:
        log.exception("Job %s failed: %s", job_id, e)
        set_failed(job_id, str(e))