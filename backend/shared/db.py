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
    """Run init SQL if tables don't exist."""
    migrations_dir = Path(__file__).resolve().parent / "migrations"
    init_sql = migrations_dir / "001_init.sql"
    if not init_sql.exists():
        return
    sql = init_sql.read_text(encoding="utf-8")
    # Run each statement (asyncpg execute() is single-statement)
    statements = [s.strip() for s in sql.split(";") if s.strip() and not s.strip().startswith("--")]
    async with pool.acquire() as conn:
        for stmt in statements:
            if stmt:
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
            await conn.execute("DELETE FROM graph_edges WHERE codebase_id = $1", codebase_id)
            await conn.execute("DELETE FROM graph_nodes WHERE codebase_id = $1", codebase_id)

        for n in nodes:
            nid = n.get("id", "")
            data = n.get("data", {})
            summary = (data.get("explanation") or data.get("summary") or "")[:15000]
            await conn.execute(
                """
                INSERT INTO graph_nodes (codebase_id, node_id, name, type, filepath, start_line, end_line, code, summary)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                ON CONFLICT (codebase_id, node_id) DO UPDATE SET
                    name = EXCLUDED.name, type = EXCLUDED.type, filepath = EXCLUDED.filepath,
                    start_line = EXCLUDED.start_line, end_line = EXCLUDED.end_line, code = EXCLUDED.code, summary = EXCLUDED.summary
                """,
                codebase_id,
                nid,
                data.get("label", nid),
                data.get("type", "function"),
                data.get("filepath", ""),
                data.get("start_line", 0),
                data.get("end_line", 0),
                (data.get("code") or "")[:10000],
                summary,
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


async def read_codebase_graph(codebase_id: str) -> Optional[dict[str, Any]]:
    """Load codebase graph from Postgres into React Flow style { nodes, edges }."""
    pool = await get_pool()
    if not pool:
        return None

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT node_id, name, type, filepath, start_line, end_line, code, summary
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
                    "explanation": r["summary"] or "",
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
    """Set summary for a graph node."""
    pool = await get_pool()
    if not pool:
        return False
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE graph_nodes SET summary = $1 WHERE codebase_id = $2 AND node_id = $3",
            summary,
            codebase_id,
            symbol_id,
        )
    return True


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
    """Keyword search over graph_nodes (name, filepath, code, summary). Optionally include program graph nodes."""
    pool = await get_pool()
    if not pool:
        return []

    q = query.lower().replace("%", "\\%").replace("_", "\\_")
    chunks: list[dict[str, Any]] = []

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT node_id, name, filepath, summary, code
            FROM graph_nodes
            WHERE codebase_id = $1
              AND (LOWER(name) LIKE $2 OR LOWER(filepath) LIKE $2 OR LOWER(COALESCE(code, '')) LIKE $2 OR LOWER(COALESCE(summary, '')) LIKE $2)
            LIMIT $3
            """,
            codebase_id,
            f"%{q}%",
            k,
        )
        for r in rows:
            chunks.append({
                "id": r["node_id"],
                "type": "symbol",
                "name": r["name"],
                "filepath": r["filepath"],
                "summary": r["summary"],
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
        rows = await conn.fetch(
            """
            SELECT node_id, name, filepath, summary, code
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
            "summary": r["summary"],
            "code": r["code"],
        }
        for r in rows
    ]