"""
Postgres service for EzDocs — users, analyses, codebase graph, program graphs.

Uses asyncpg. Run migrations from shared/migrations/001_init.sql on first use.
"""

import json
import logging
from pathlib import Path
from typing import Any, Optional

from shared.config import DATABASE_URL

log = logging.getLogger("ezdocs.db")

_pool: Any = None


def _compute_line_col(content: str, offset: int) -> tuple[int, int]:
    line = content.count("\n", 0, offset) + 1
    last_newline = content.rfind("\n", 0, offset)
    col = offset + 1 if last_newline == -1 else offset - last_newline
    return line, col


def _slice_explanation(content: str, offset: int | None, length: int | None) -> str:
    if offset is None or length is None or offset < 0 or length <= 0:
        return ""
    return content[offset: offset + length]


def _build_explanations_blob(nodes: list[dict[str, Any]]) -> tuple[str, dict[str, dict[str, int]]]:
    sections: list[str] = []
    refs: dict[str, dict[str, int]] = {}
    cursor = 0

    for node in nodes:
        nid = node.get("id", "")
        data = node.get("data", {})
        explanation = (data.get("explanation") or data.get("summary") or "").strip()
        if not nid or not explanation:
            continue

        label = data.get("label", nid)
        filepath = data.get("filepath", "")
        section = f"## {label}\n{filepath}\n{explanation}\n\n"
        explanation_offset = cursor + len(f"## {label}\n{filepath}\n")
        line, col = _compute_line_col(section, len(f"## {label}\n{filepath}\n"))
        refs[nid] = {
            "line": 0,
            "col": col,
            "offset": explanation_offset,
            "length": len(explanation),
        }
        sections.append(section)
        cursor += len(section)

    content = "".join(sections)
    if refs:
        for node_id, ref in refs.items():
            line, col = _compute_line_col(content, ref["offset"])
            ref["line"] = line
            ref["col"] = col
    return content, refs


def _pool_is_alive(pool) -> bool:
    """Return False if the pool is closed or bound to a dead event loop."""
    try:
        if pool is None or pool._closed:
            return False
        import asyncio
        loop = asyncio.get_event_loop()
        return not loop.is_closed()
    except Exception:
        return False


async def get_pool():
    """Lazy asyncpg pool. Returns None if DATABASE_URL not set or connection fails.
    Recreates the pool automatically if the existing one is bound to a closed event loop."""
    global _pool
    if _pool is not None and _pool_is_alive(_pool):
        return _pool

    # Pool is stale or missing — reset and recreate
    if _pool is not None:
        log.warning("Detected stale asyncpg pool (event loop replaced). Recreating…")
        try:
            await _pool.close()
        except Exception:
            pass
        _pool = None

    if not DATABASE_URL or DATABASE_URL == "postgresql://":
        log.debug("DATABASE_URL not set; Postgres disabled.")
        return None
    try:
        import asyncpg
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10, command_timeout=60)
        await _init_schema(_pool)
        log.info("Postgres pool connected")
        return _pool
    except Exception as e:
        log.warning("Postgres connection failed: %s", e)
        return None


async def close_pool() -> None:
    """Gracefully close the connection pool. Called from app shutdown lifespan."""
    global _pool
    if _pool is not None:
        try:
            await _pool.close()
            log.info("Postgres pool closed.")
        except Exception as e:
            log.warning("Error closing Postgres pool: %s", e)
        _pool = None


async def _init_schema(pool) -> None:
    """Run all migration SQL files in sorted order."""
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    if not migrations_dir.exists():
        return
    sql_files = sorted(migrations_dir.glob("*.sql"))
    async with pool.acquire() as conn:
        for sql_file in sql_files:
            sql = sql_file.read_text(encoding="utf-8")
            # Strip single-line comments before splitting on semicolons to avoid
            # semicolons inside comments (e.g. "-- foo; bar") breaking the parse.
            lines = [l for l in sql.splitlines() if not l.strip().startswith("--")]
            statements = [s.strip() for s in "\n".join(lines).split(";") if s.strip()]
            for stmt in statements:
                try:
                    await conn.execute(stmt)
                except Exception as e:
                    if "already exists" not in str(e).lower():
                        log.warning("Migration statement failed: %s", e)


def _is_available_sync() -> bool:
    """Sync check: True if DATABASE_URL is set (pool may not be created yet)."""
    return bool(DATABASE_URL and "postgresql" in DATABASE_URL)


async def is_available() -> bool:
    """Async check: True if Postgres is reachable."""
    return await get_pool() is not None


# ─── Codebase graph ───────────────────────────────────────────────────────────

async def write_codebase_graph(
    codebase_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    user_id: Optional[str] = None,
    source_path: str = "",
    clear_first: bool = True,
) -> bool:
    """Persist codebase graph to Postgres. nodes: React Flow style; edges: { source, target, label? }."""
    pool = await get_pool()
    if not pool:
        return False

    async with pool.acquire() as conn:
        if clear_first:
            await conn.execute("DELETE FROM codebase_explanations WHERE codebase_id = $1", codebase_id)
            await conn.execute("DELETE FROM graph_edges WHERE codebase_id = $1", codebase_id)
            await conn.execute("DELETE FROM graph_nodes WHERE codebase_id = $1", codebase_id)

        for n in nodes:
            nid = n.get("id", "")
            data = n.get("data", {})
            await conn.execute(
                """
                INSERT INTO graph_nodes (
                    codebase_id, node_id, name, type, filepath, start_line, end_line, code,
                    summary, explanation_line, explanation_col, explanation_offset, explanation_length
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, NULL, NULL, NULL, NULL, NULL)
                ON CONFLICT (codebase_id, node_id) DO UPDATE SET
                    name = EXCLUDED.name, type = EXCLUDED.type, filepath = EXCLUDED.filepath,
                    start_line = EXCLUDED.start_line, end_line = EXCLUDED.end_line, code = EXCLUDED.code,
                    summary = NULL,
                    explanation_line = NULL,
                    explanation_col = NULL,
                    explanation_offset = NULL,
                    explanation_length = NULL
                """,
                codebase_id,
                nid,
                data.get("label", nid),
                data.get("type", "function"),
                data.get("filepath", ""),
                data.get("start_line", 0),
                data.get("end_line", 0),
                (data.get("code") or "")[:10000],
            )

        for e in edges:
            await conn.execute(
                """
                INSERT INTO graph_edges (codebase_id, source_id, target_id, edge_type)
                VALUES ($1, $2, $3, $4)
                ON CONFLICT (codebase_id, source_id, target_id) DO NOTHING
                """,
                codebase_id,
                e.get("source", ""),
                e.get("target", ""),
                e.get("label", "CALLS"),
            )

        if user_id and source_path:
            await conn.execute(
                """
                INSERT INTO analyses (user_id, codebase_id, source_path)
                VALUES ($1, $2, $3)
                ON CONFLICT (codebase_id) DO UPDATE SET source_path = EXCLUDED.source_path, user_id = EXCLUDED.user_id
                """,
                user_id,
                codebase_id,
                source_path,
            )

    log.info("Wrote codebase graph %s: %d nodes, %d edges", codebase_id, len(nodes), len(edges))
    return True


async def write_codebase_explanations(codebase_id: str, nodes: list[dict[str, Any]]) -> bool:
    pool = await get_pool()
    if not pool:
        return False

    content, refs = _build_explanations_blob(nodes)

    async with pool.acquire() as conn:
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

    return True


async def list_analyses(user_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    """List saved codebase analyses (codebase_id, source_path, created_at). Optionally filter by user_id."""
    pool = await get_pool()
    if not pool:
        return []
    async with pool.acquire() as conn:
        if user_id:
            rows = await conn.fetch(
                "SELECT codebase_id, source_path, created_at FROM analyses WHERE user_id = $1 ORDER BY created_at DESC LIMIT $2",
                user_id,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT codebase_id, source_path, created_at FROM analyses ORDER BY created_at DESC LIMIT $1",
                limit,
            )
    return [
        {"codebase_id": r["codebase_id"], "source_path": r["source_path"], "created_at": r["created_at"]}
        for r in rows
    ]


async def explanation_progress(codebase_id: str) -> Optional[dict[str, Any]]:
    """Return { total, explained, pending } counts for a codebase."""
    pool = await get_pool()
    if not pool:
        return None
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                COUNT(*) AS total,
                COUNT(*) FILTER (WHERE explanation_offset IS NOT NULL) AS explained
            FROM graph_nodes
            WHERE codebase_id = $1
            """,
            codebase_id,
        )
    if not row or row["total"] == 0:
        return None
    total = row["total"]
    explained = row["explained"]
    return {"total": total, "explained": explained, "pending": total - explained}


async def read_codebase_graph(codebase_id: str) -> Optional[dict[str, Any]]:
    """Load codebase graph from Postgres into React Flow style { nodes, edges }."""
    pool = await get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        explanation_row = await conn.fetchrow(
            "SELECT content FROM codebase_explanations WHERE codebase_id = $1",
            codebase_id,
        )
        explanation_content = explanation_row["content"] if explanation_row else ""
        rows = await conn.fetch(
            """
            SELECT node_id, name, type, filepath, start_line, end_line, code,
                   explanation_line, explanation_col, explanation_offset, explanation_length
            FROM graph_nodes WHERE codebase_id = $1
            """,
            codebase_id,
        )
        if not rows:
            return None

        nodes = [
            {
                "id": r["node_id"],
                "type": "ez",
                "data": {
                    "label": r["name"],
                    "type": r["type"],
                    "filepath": r["filepath"] or "",
                    "start_line": r["start_line"] or 0,
                    "end_line": r["end_line"] or 0,
                    "code": r["code"] or "",
                    "explanation": _slice_explanation(explanation_content, r["explanation_offset"], r["explanation_length"]),
                    "explanation_line": r["explanation_line"],
                    "explanation_col": r["explanation_col"],
                    "explanation_offset": r["explanation_offset"],
                    "explanation_length": r["explanation_length"],
                },
                "position": {"x": 0, "y": 0},
            }
            for r in rows
        ]

        edge_rows = await conn.fetch(
            "SELECT source_id, target_id, edge_type FROM graph_edges WHERE codebase_id = $1",
            codebase_id,
        )
        edges = [
            {
                "id": f"e-{r['source_id']}-{r['target_id']}",
                "source": r["source_id"],
                "target": r["target_id"],
                "type": "ez",
                "label": r["edge_type"] or "CALLS",
            }
            for r in edge_rows
        ]

    return {"nodes": nodes, "edges": edges}


async def set_symbol_summary(codebase_id: str, symbol_id: str, summary: str) -> bool:
    """Backward-compatible single-node explanation write into the shared codebase blob."""
    pool = await get_pool()
    if not pool:
        return False
    graph = await read_codebase_graph(codebase_id)
    if not graph:
        return False
    for node in graph.get("nodes", []):
        if node.get("id") == symbol_id:
            node.setdefault("data", {})["explanation"] = summary
            break
    return await write_codebase_explanations(codebase_id, graph.get("nodes", []))


# ─── Program graph ───────────────────────────────────────────────────────────

async def write_program_graph(
    program_id: str,
    nodes: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    *,
    user_id: str = "",
    name: Optional[str] = None,
    clear_first: bool = True,
) -> bool:
    """Store program graph. nodes: { id, content, label?, order? }; edges: { source_id, target_id }."""
    pool = await get_pool()
    if not pool:
        return False

    nodes_json = json.dumps(nodes)
    edges_json = json.dumps(edges)

    async with pool.acquire() as conn:
        if clear_first:
            await conn.execute("DELETE FROM program_graphs WHERE program_id = $1", program_id)
        await conn.execute(
            """
            INSERT INTO program_graphs (user_id, program_id, name, nodes, edges)
            VALUES ($1, $2, $3, $4::jsonb, $5::jsonb)
            ON CONFLICT (program_id) DO UPDATE SET
                user_id = EXCLUDED.user_id,
                name = EXCLUDED.name,
                nodes = EXCLUDED.nodes,
                edges = EXCLUDED.edges
            """,
            user_id,
            program_id,
            name or program_id,
            nodes_json,
            edges_json,
        )

    log.info("Wrote program graph %s: %d nodes, %d edges", program_id, len(nodes), len(edges))
    return True


async def read_program_graph(program_id: str) -> Optional[dict[str, Any]]:
    """Return { nodes: [...], edges: [...] } with id, content, summary, label, order."""
    pool = await get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT nodes, edges FROM program_graphs WHERE program_id = $1",
            program_id,
        )
    if not row:
        return None

    nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else list(row["nodes"])
    edges = json.loads(row["edges"]) if isinstance(row["edges"], str) else list(row["edges"])
    return {"nodes": nodes, "edges": edges}


async def list_program_graphs_by_user(user_id: str, limit: int = 100) -> list[dict[str, Any]]:
    """List program graphs owned by user. Returns [{ program_id, name, created_at }]."""
    pool = await get_pool()
    if not pool or not user_id:
        return []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT program_id, name, created_at
            FROM program_graphs
            WHERE user_id = $1
            ORDER BY created_at DESC
            LIMIT $2
            """,
            user_id,
            limit,
        )
    return [
        {
            "program_id": r["program_id"],
            "name": r["name"] or r["program_id"],
            "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
        }
        for r in rows
    ]


async def set_program_node_summary(program_id: str, node_id: str, summary: str) -> bool:
    """Update summary for one node in a program graph (stored in JSONB nodes)."""
    pool = await get_pool()
    if not pool:
        return False

    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT nodes FROM program_graphs WHERE program_id = $1", program_id)
    if not row:
        return False

    nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else list(row["nodes"])
    for n in nodes:
        if n.get("id") == node_id:
            n["summary"] = summary
            break
    nodes_json = json.dumps(nodes)
    async with pool.acquire() as conn:
        await conn.execute("UPDATE program_graphs SET nodes = $1::jsonb WHERE program_id = $2", nodes_json, program_id)
    return True


# ─── RAG (keyword search over graph_nodes) ─────────────────────────────────────

async def rag_query_keyword(
    codebase_id: str,
    query: str,
    k: int = 10,
    program_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Keyword search over graph nodes. Optionally include program graph nodes."""
    pool = await get_pool()
    if not pool:
        return []

    q = query.lower().replace("%", "\\%").replace("_", "\\_")
    chunks: list[dict[str, Any]] = []

    async with pool.acquire() as conn:
        explanation_row = await conn.fetchrow(
            "SELECT content FROM codebase_explanations WHERE codebase_id = $1",
            codebase_id,
        )
        explanation_content = explanation_row["content"] if explanation_row else ""
        rows = await conn.fetch(
            """
            SELECT node_id, name, filepath, code, explanation_offset, explanation_length
            FROM graph_nodes
            WHERE codebase_id = $1
              AND (LOWER(name) LIKE $2 OR LOWER(filepath) LIKE $2 OR LOWER(COALESCE(code, '')) LIKE $2)
            LIMIT $3
            """,
            codebase_id,
            f"%{q}%",
            k,
        )
        for r in rows:
            explanation = _slice_explanation(explanation_content, r["explanation_offset"], r["explanation_length"])
            chunks.append({
                "id": r["node_id"],
                "type": "symbol",
                "name": r["name"],
                "filepath": r["filepath"],
                "summary": explanation,
                "code": r["code"],
            })

        if program_id and len(chunks) < k:
            row = await conn.fetchrow("SELECT nodes FROM program_graphs WHERE program_id = $1", program_id)
            if row:
                nodes = json.loads(row["nodes"]) if isinstance(row["nodes"], str) else list(row["nodes"])
                for n in nodes:
                    if len(chunks) >= k:
                        break
                    content = (n.get("content") or "") + " " + (n.get("summary") or "")
                    if q in content.lower():
                        chunks.append({
                            "id": n.get("id", ""),
                            "type": "program_node",
                            "name": n.get("label"),
                            "summary": n.get("summary"),
                            "content": n.get("content"),
                        })

    return chunks[:k]


async def get_graph_nodes_by_ids(codebase_id: str, symbol_ids: list[str]) -> list[dict[str, Any]]:
    """Fetch graph nodes by codebase_id and list of node_ids. Returns list of { id, name, filepath, summary, code }."""
    pool = await get_pool()
    if not pool or not symbol_ids:
        return []
    async with pool.acquire() as conn:
        explanation_row = await conn.fetchrow(
            "SELECT content FROM codebase_explanations WHERE codebase_id = $1",
            codebase_id,
        )
        explanation_content = explanation_row["content"] if explanation_row else ""
        rows = await conn.fetch(
            """
            SELECT node_id, name, filepath, code, explanation_offset, explanation_length
            FROM graph_nodes
            WHERE codebase_id = $1 AND node_id = ANY($2::text[])
            """,
            codebase_id,
            symbol_ids,
        )
    return [
        {
            "id": r["node_id"],
            "type": "symbol",
            "name": r["name"],
            "filepath": r["filepath"],
            "summary": _slice_explanation(explanation_content, r["explanation_offset"], r["explanation_length"]),
            "code": r["code"],
        }
        for r in rows
    ]