"""Graph analysis and file explorer endpoints."""

import asyncio
import logging
import shutil
from pathlib import Path
from tempfile import mkdtemp

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect

from shared import crawler, ingest
from shared.config import DEFAULT_MAX_FILES, MAX_FILES_CEILING
from graph.builder import GraphBuilder
from shared.schemas import GithubRequest
from shared.state import graph_cache, get_parser

from shared import db

log = logging.getLogger("ezdocs")

# ─── Constants ───────────────────────────────────────────────────────────────

IGNORED_DIRS = {
    ".git", ".hg", ".svn",
    "node_modules",
    "venv", ".venv", "env", ".env",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    "dist", "build", "out", "target",
    "site-packages", "dist-packages",
}

# ─── Helpers ───────────────────────────────────────────────────────────────────

def resolve_local_path(raw: str) -> Path:
    """Resolve and validate a local path; raise HTTPException on failure."""
    p = Path(raw).resolve()
    if not p.exists():
        raise HTTPException(status_code=404, detail=f"Path not found: {p}")
    if not p.is_dir():
        raise HTTPException(status_code=400, detail=f"Path is not a directory: {p}")
    return p


def build_graph_for(path: str, max_files: int = 200, codebase_id: str | None = None, user_id: str | None = None) -> dict:
    """Build and return a React Flow graph JSON for the given local path.
    Optionally prefetches explanations (see PREFETCH_EXPLANATIONS). If codebase_id is set, caller persists via db.write_codebase_graph."""
    from shared.config import PREFETCH_EXPLANATIONS
    import shared.ai as ai
    builder = GraphBuilder(path)
    builder.build_graph(max_files=max_files)
    result = builder.to_json()
    if PREFETCH_EXPLANATIONS:
        try:
            ai.prefetch_explanations_sync(result["nodes"], result.get("edges"))
        except Exception as e:
            log.warning("Prefetch explanations skipped: %s", e)
    graph_cache["graph"] = result
    return result


def file_tree(root: Path) -> list[dict]:
    """Recursively build a file-explorer tree, filtering ignored dirs."""
    parser = get_parser()
    items: list[dict] = []
    try:
        entries = sorted(root.iterdir(), key=lambda e: (e.is_file(), e.name.lower()))
    except PermissionError:
        return items
    for entry in entries:
        if entry.name in IGNORED_DIRS or entry.name.startswith("."):
            continue
        rel = str(entry.relative_to(root))
        if entry.is_dir():
            children = file_tree(entry)
            if children:
                items.append({"name": entry.name, "type": "folder", "path": rel, "children": children})
        elif parser.is_supported(str(entry)):
            items.append({"name": entry.name, "type": "file", "path": rel})
    return items


# ─── Router ───────────────────────────────────────────────────────────────────

router = APIRouter(tags=["graph", "explorer"])


@router.get("/analyze")
async def analyze_local(
    path: str = Query(..., description="Absolute or relative path to the codebase"),
    max_files: int = Query(200, ge=1, le=MAX_FILES_CEILING, description="Max files to parse"),
    codebase_id: str | None = Query(None, description="If set, persist graph to Postgres under this id"),
    user_id: str | None = Query(None, description="Optional user id (Clerk) to associate analysis"),
) -> dict:
    """Analyse a local codebase and return a React Flow dependency graph."""
    resolved = resolve_local_path(path)
    log.info("Analyzing local path: %s (max_files=%d)", resolved, max_files)
    try:
        result = build_graph_for(str(resolved), max_files=max_files, codebase_id=codebase_id, user_id=user_id)
        if codebase_id:
            try:
                if await db.is_available():
                    await db.write_codebase_graph(
                        codebase_id,
                        result["nodes"],
                        result["edges"],
                        user_id=user_id or "",
                        source_path=str(resolved),
                        clear_first=True,
                    )
            except Exception as e:
                log.warning("Postgres persist skipped for %s: %s", codebase_id, e)
        if codebase_id:
            result["codebase_id"] = codebase_id
        return result
    except Exception as exc:
        log.exception("Error analyzing %s", resolved)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {exc}") from exc


@router.post("/analyze/github")
async def analyze_github(request: GithubRequest) -> dict:
    """Clone a GitHub repo, analyse it, and return the dependency graph."""
    log.info("Analyzing GitHub repo: %s", request.url)
    try:
        path = ingest.clone_github_repo(request.url)
        return build_graph_for(path)
    except Exception as exc:
        log.exception("GitHub analysis failed for %s", request.url)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/analyze/upload")
async def analyze_upload(file: UploadFile = File(...)) -> dict:
    """Upload a .zip archive, extract it, and return the dependency graph."""
    if not file.filename or not file.filename.endswith(".zip"):
        raise HTTPException(status_code=400, detail="Only .zip archives are supported.")
    log.info("Processing upload: %s", file.filename)
    try:
        path = await ingest.process_upload(file)
        return build_graph_for(path)
    except Exception as exc:
        log.exception("Upload analysis failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/files")
async def list_files(
    path: str = Query(..., description="Local path to the codebase"),
) -> list:
    """Return a recursive file-explorer tree for the given directory."""
    root = resolve_local_path(path)
    return file_tree(root)


@router.get("/analyses")
async def list_analyses(
    user_id: str | None = Query(None, description="Filter by user id (e.g. Clerk sub)"),
    limit: int = Query(100, ge=1, le=500),
) -> list[dict]:
    """List saved codebase analyses for loading graphs from DB."""
    try:
        items = await db.list_analyses(user_id=user_id, limit=limit)
        return items
    except Exception as exc:
        log.exception("List analyses failed: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/graph")
async def get_graph_from_db(
    codebase_id: str = Query(..., description="Load graph from Postgres by codebase id"),
) -> dict:
    """Load a previously persisted graph from Postgres (if available)."""
    try:
        if not await db.is_available():
            raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
        data = await db.read_codebase_graph(codebase_id)
        if data is None:
            raise HTTPException(status_code=404, detail=f"No graph found for codebase_id={codebase_id}")
        return data
    except HTTPException:
        raise
    except Exception as exc:
        log.exception("Failed to load graph for %s", codebase_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.websocket("/ws/analyze/github")
async def ws_analyze_github(websocket: WebSocket) -> None:
    """Stream GitHub repo analysis over WebSocket."""
    await websocket.accept()
    temp_dir: Path | None = None
    try:
        data = await asyncio.wait_for(websocket.receive_json(), timeout=30)
        repo_url: str = data.get("url", "").strip()
        if not repo_url:
            await websocket.send_json({"type": "error", "message": "No URL provided"})
            return
        log.info("WS GitHub analysis: %s", repo_url)
        temp_dir = Path(mkdtemp(prefix="ezdocs_stream_"))
        gh_crawler = crawler.GitHubCrawler(repo_url)
        builder = GraphBuilder(str(temp_dir))
        async for batch in gh_crawler.stream_files():
            new_nodes: list[dict] = []
            for file_data in batch:
                file_path = temp_dir / file_data["path"]
                file_path.parent.mkdir(parents=True, exist_ok=True)
                try:
                    file_path.write_text(file_data["content"], encoding="utf-8")
                except OSError as exc:
                    log.warning("Could not write %s: %s", file_path, exc)
                    continue
                try:
                    relative = file_path.relative_to(temp_dir)
                    for item in builder.parser.parse_file(str(file_path)):
                        node_id = f"{relative}::{item['name']}"
                        builder.graph.add_node(
                            node_id,
                            name=item["name"],
                            type=item["type"],
                            filepath=str(relative),
                            start_line=item["start_line"],
                            end_line=item["end_line"],
                            code=item["code"],
                        )
                        new_nodes.append({
                            "id": node_id,
                            "data": {
                                "label": item["name"],
                                "type": item["type"],
                                "filepath": str(relative),
                                "start_line": item["start_line"],
                                "end_line": item["end_line"],
                                "code": item["code"],
                            },
                        })
                except Exception as exc:
                    log.warning("Parse error for %s: %s", file_path, exc)
            if new_nodes:
                await websocket.send_json({"type": "update", "nodes": new_nodes, "edges": []})
        builder._create_edges()
        graph = builder.to_json()
        log.info("WS GitHub complete: %d nodes, %d edges", len(graph["nodes"]), len(graph["edges"]))
        graph_cache["graph"] = graph
        await websocket.send_json({"type": "complete", "graph": graph})
    except asyncio.TimeoutError:
        await websocket.send_json({"type": "error", "message": "Timed out waiting for request"})
    except WebSocketDisconnect:
        log.info("WS client disconnected during GitHub analysis")
    except Exception as exc:
        log.exception("WS GitHub analysis error")
        try:
            await websocket.send_json({"type": "error", "message": str(exc)})
        except Exception:
            pass
    finally:
        if temp_dir and temp_dir.exists():
            shutil.rmtree(temp_dir, ignore_errors=True)
        try:
            await websocket.close()
        except Exception:
            pass
