"""
Job handlers for the in-process job queue.
Run in worker thread: pop job, dispatch by type, set result/failed.

Job types:
  graph_analyze — parse codebase, persist to Postgres, return graph immediately,
                  then explain root-layer nodes first and remaining in background.
  graph_explain — batch-generate explanations for user-code nodes
"""

import asyncio
import logging
import threading
from pathlib import Path

from shared.config import MAX_FILES_CEILING, DATABASE_URL
from shared import db
from shared.jobqueue import set_failed, set_progress, set_result, set_running
from shared.request_control import raise_if_cancelled
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
                await conn.execute("DELETE FROM codebase_explanations WHERE codebase_id = $1", codebase_id)
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
                        None,
                        None,
                        None,
                        None,
                        None,
                    ))
                await conn.copy_records_to_table(
                    "graph_nodes",
                    records=node_records,
                    columns=["codebase_id", "node_id", "name", "type", "filepath",
                             "start_line", "end_line", "code", "summary",
                             "explanation_line", "explanation_col", "explanation_offset", "explanation_length"],
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


def _persist_explanations(codebase_id: str, nodes: list[dict]) -> None:
    """Write explanation blob to Postgres using a direct connection (own event loop)."""
    if not DATABASE_URL or "postgresql" not in DATABASE_URL:
        log.warning("Postgres unavailable; explanations for %s not persisted.", codebase_id)
        return

    async def _write():
        import asyncpg
        conn = await asyncpg.connect(DATABASE_URL)
        try:
            content, refs = db._build_explanations_blob(nodes)
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO codebase_explanations (codebase_id, content)
                    VALUES ($1, $2)
                    ON CONFLICT (codebase_id) DO UPDATE SET content = EXCLUDED.content
                    """,
                    codebase_id,
                    content,
                )
                for node in nodes:
                    nid = node.get("id", "")
                    ref = refs.get(nid)
                    await conn.execute(
                        """
                        UPDATE graph_nodes
                        SET summary = NULL,
                            explanation_line = $1,
                            explanation_col = $2,
                            explanation_offset = $3,
                            explanation_length = $4
                        WHERE codebase_id = $5 AND node_id = $6
                        """,
                        ref.get("line") if ref else None,
                        ref.get("col") if ref else None,
                        ref.get("offset") if ref else None,
                        ref.get("length") if ref else None,
                        codebase_id,
                        nid,
                    )
            log.info("Persisted explanations for %s: %d refs", codebase_id, len(refs))
        finally:
            await conn.close()

    _run_async(_write())


def _update_node_summary(codebase_id: str, node_id: str, summary: str) -> None:
    raise NotImplementedError("Per-node summary updates replaced by codebase explanation blob writes")


# ─── graph_analyze ────────────────────────────────────────────────────────────

def _sort_nodes_root_first(nodes: list[dict]) -> list[dict]:
    """Sort nodes so layer-0 (root files) come first, then layer-1, then rest."""
    def _layer_key(n: dict) -> int:
        data = n.get("data") or {}
        layer = data.get("layer")
        if layer is not None:
            return int(layer)
        if data.get("is_root_file"):
            return 0
        if data.get("is_root_dep"):
            return 1
        return 2
    return sorted(nodes, key=_layer_key)


def _run_graph_analyze(payload: dict, job_id: str | None = None) -> dict:
    path        = payload.get("path")
    url         = payload.get("url")
    raw_max_files = int(payload.get("max_files", 0))
    max_files   = 0 if raw_max_files <= 0 else min(raw_max_files, MAX_FILES_CEILING)
    codebase_id = payload.get("codebase_id")
    user_id     = payload.get("user_id") or ""

    if url:
        source_path = ingest.clone_github_repo(url)
    elif path:
        source_path = str(_resolve_path(path))
    else:
        raise ValueError("Either path or url is required")

    if job_id:
        set_progress(
            job_id,
            phase="analyzing",
            message="Scanning files and building dependency graph...",
            source_path=source_path,
        )

    result = build_graph_for(
        source_path, max_files=max_files,
        codebase_id=codebase_id, user_id=user_id or None,
    )

    if job_id:
        set_progress(
            job_id,
            phase="persisting",
            message="Saving graph snapshot...",
            node_count=len(result.get("nodes", [])),
            edge_count=len(result.get("edges", [])),
        )

    if codebase_id:
        _persist_graph(codebase_id, result["nodes"], result["edges"], user_id, source_path)

    # Mark job done NOW so frontend can render the graph immediately.
    graph_result = {
        "codebase_id": codebase_id,
        "nodes": result.get("nodes", []),
        "edges": result.get("edges", []),
        "node_count":  len(result.get("nodes", [])),
        "edge_count":  len(result.get("edges", [])),
        "source_path": source_path,
    }

    if job_id:
        set_progress(
            job_id,
            phase="complete",
            message="Graph is ready. Explanations generating in background...",
            node_count=len(result.get("nodes", [])),
            edge_count=len(result.get("edges", [])),
            explaining=True,
        )

    # Fire off background explanation: root-layer nodes first, then the rest.
    # Each batch persists to DB immediately so the frontend can poll /graph
    # and see explanations appear incrementally.
    if codebase_id and result.get("nodes"):
        sorted_nodes = _sort_nodes_root_first(result["nodes"])
        explain_thread = threading.Thread(
            target=_run_graph_explain_background,
            args=(codebase_id, sorted_nodes),
            daemon=True,
        )
        explain_thread.start()

    return graph_result


# ─── graph_explain (background, root-first) ──────────────────────────────────

def _run_graph_explain_background(codebase_id: str, sorted_nodes: list[dict]) -> None:
    """
    Background thread: generate explanations root-first (layer 0 → 1 → 2+).
    After each batch, persist updated explanation blob + pointers to Postgres
    so the frontend sees explanations appear incrementally.
    """
    try:
        _run_graph_explain({
            "codebase_id": codebase_id,
            "nodes": sorted_nodes,
        })
    except Exception as e:
        log.warning("Background explanation failed for %s: %s", codebase_id, e)


def _run_graph_explain(payload: dict, job_id: str | None = None) -> dict:
    """
    Batch-generate summaries for user-code nodes that have code but no explanation.
    Library blob nodes are skipped — they already have fixed blurbs from GraphBuilder.
    Nodes are processed in the order given (caller should sort root-first).
    After each batch, the explanation blob is persisted to Postgres immediately.
    """
    codebase_id = payload.get("codebase_id")
    if not codebase_id:
        raise ValueError("codebase_id required for graph_explain")

    nodes: list[dict] = payload.get("nodes") or []

    explain_nodes = [
        n for n in nodes
        if n.get("type") != "library"
        and (n.get("data") or {}).get("code", "").strip()
        and not (n.get("data") or {}).get("explanation", "").strip()
    ]

    log.info("graph_explain: %d nodes to explain for codebase %s (root-first order)",
             len(explain_nodes), codebase_id)

    if job_id:
        set_progress(
            job_id,
            phase="explaining",
            message=f"Generating explanations for {len(explain_nodes)} nodes...",
            explained_done=0,
            explained_failed=0,
            explained_total=len(explain_nodes),
        )

    from shared.ai import generate_summary, AIProviderError

    done = failed = 0
    generated: dict[str, str] = {}
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
                generated[nid] = summary
                done += 1
            except AIProviderError as e:
                log.warning("Explain failed for %s: %s", name, e)
                failed += 1
            except Exception as e:
                log.warning("Unexpected explain error for %s: %s", name, e)
                failed += 1

        batch_num = i // _EXPLAIN_BATCH_SIZE + 1
        total_batches = (len(explain_nodes) + _EXPLAIN_BATCH_SIZE - 1) // _EXPLAIN_BATCH_SIZE
        log.info("Explanations progress: %d done, %d failed (batch %d/%d)",
                 done, failed, batch_num, total_batches)

        if job_id:
            set_progress(
                job_id,
                phase="explaining",
                message=(
                    f"Generating explanations... {done}/{len(explain_nodes)} complete"
                    if explain_nodes else
                    "No node explanations were needed."
                ),
                explained_done=done,
                explained_failed=failed,
                explained_total=len(explain_nodes),
            )

        # Persist after every batch so explanations appear incrementally in DB.
        if generated:
            hydrated = _hydrate_nodes_with_explanations(nodes, generated)
            try:
                _persist_explanations(codebase_id, hydrated)
                log.info("Incremental persist after batch %d: %d explanations for %s",
                         batch_num, len(generated), codebase_id)
            except Exception as e:
                log.warning("Incremental explanation persist failed for %s: %s", codebase_id, e)

    return {"codebase_id": codebase_id, "explained": done, "failed": failed}


def _hydrate_nodes_with_explanations(nodes: list[dict], generated: dict[str, str]) -> list[dict]:
    """Merge generated explanations into node copies for blob persistence."""
    hydrated: list[dict] = []
    for node in nodes:
        copied = dict(node)
        copied_data = dict(node.get("data") or {})
        if node.get("id") in generated:
            copied_data["explanation"] = generated[node["id"]]
        copied["data"] = copied_data
        hydrated.append(copied)
    return hydrated


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
            result = _run_graph_analyze(payload, job_id=job_id)
            set_result(job_id, result)
        elif job_type == "graph_explain":
            result = _run_graph_explain(payload, job_id=job_id)
            set_result(job_id, result)
            log.info("Explanation job %s complete: %s", job_id, result)
        else:
            set_failed(job_id, f"Unknown job type: {job_type}")
    except Exception as e:
        log.exception("Job %s failed: %s", job_id, e)
        set_failed(job_id, str(e))