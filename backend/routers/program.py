"""Program graph (user-defined intent nodes), summarization, and code generation."""

import logging

from fastapi import APIRouter, Depends, HTTPException, Query

from shared import db, mongo_service
from shared.auth import get_current_user_optional
from shared.llm_providers import completion
from shared.schemas import (
    ApiKeysBody,
    GenerateCodeRequest,
    ProgramGraphRequest,
    ProgramSummarizeRequest,
)

log = logging.getLogger("ezdocs.program")

router = APIRouter(tags=["program", "codegen"])

_SUMMARY_SYSTEM = (
    "You are a precise technical writer. Given a user's description of what a part of a program should do, "
    "output a concise summary in 2–4 sentences or bullet points. Be specific and actionable; no fluff."
)


@router.post("/program")
async def create_or_update_program(
    request: ProgramGraphRequest,
    clerk_user_id: str | None = Depends(get_current_user_optional),
) -> dict:
    """Create or replace a program graph (user-defined intent nodes) in Postgres."""
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    nodes = [{"id": n.id, "content": n.content, "label": n.label, "order": n.order} for n in request.nodes]
    edges = [{"source_id": e.source_id, "target_id": e.target_id} for e in request.edges]
    user_id = clerk_user_id or request.user_id or ""
    ok = await db.write_program_graph(
        request.program_id,
        nodes,
        edges,
        user_id=user_id,
        clear_first=True,
    )
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to write program graph.")
    return {"program_id": request.program_id, "nodes": len(nodes), "edges": len(edges)}


@router.get("/program")
async def get_program(program_id: str = Query(..., description="Program graph id")) -> dict:
    """Return the program graph (nodes with content and summary, edges)."""
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    data = await db.read_program_graph(program_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Program {program_id} not found.")
    return data


@router.get("/program/list")
async def list_my_program_graphs(
    limit: int = Query(50, ge=1, le=200),
    clerk_user_id: str | None = Depends(get_current_user_optional),
) -> list:
    """List program graphs created by the current user. Returns [] if not authenticated."""
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    if not clerk_user_id:
        return []
    return await db.list_program_graphs_by_user(clerk_user_id, limit=limit)


@router.post("/program/summarize")
async def summarize_program(request: ProgramSummarizeRequest) -> dict:
    """
    For each program node with content, call the LLM to produce a concise summary
    and store it in Postgres. Uses the provider and optional API keys from the request.
    """
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    data = await db.read_program_graph(request.program_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Program {request.program_id} not found.")

    summarized = 0
    for node in data["nodes"]:
        content = (node.get("content") or "").strip()
        if not content:
            continue
        try:
            out = await completion(
                request.provider,
                [
                    {"role": "system", "content": _SUMMARY_SYSTEM},
                    {"role": "user", "content": content},
                ],
                model=request.model,
                api_keys=request.api_keys,
                max_tokens=512,
            )
            await db.set_program_node_summary(request.program_id, node["id"], out.strip())
            summarized += 1
        except Exception as e:
            log.warning("Summarization failed for node %s: %s", node["id"], e)

    return {"program_id": request.program_id, "summarized": summarized}


@router.post("/generate/code")
async def generate_code(
    request: GenerateCodeRequest,
    clerk_user_id: str | None = Depends(get_current_user_optional),
) -> dict:
    """
    Generate code or a project from the summarized program graph.
    Optionally uses RAG over a codebase. Saves result to MongoDB (5MB limit); returns generation_id and artifacts.
    """
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    program = await db.read_program_graph(request.program_id)
    if program is None:
        raise HTTPException(status_code=404, detail=f"Program {request.program_id} not found.")

    # Build context: summarized intents
    parts = []
    for n in program["nodes"]:
        summary = n.get("summary") or n.get("content") or ""
        if summary:
            parts.append(f"- [{n.get('label') or n['id']}]: {summary}")
    program_context = "\n".join(parts) if parts else "No summarized intents."

    # Optional RAG context from codebase (Postgres + optional Milvus)
    rag_context = ""
    if request.codebase_id:
        chunks = await db.rag_query_keyword(
            request.codebase_id,
            request.target_language + " " + (request.stack or ""),
            k=5,
        )
        if chunks:
            rag_context = "Relevant existing code/summaries:\n"
            for c in chunks:
                if c.get("summary"):
                    rag_context += f"- {c.get('name', c['id'])}: {c['summary']}\n"
                if c.get("code"):
                    rag_context += f"```\n{c['code'][:800]}\n```\n"

    user_msg = (
        f"Generate a {request.target_language} project (stack: {request.stack or 'default'}) from the following intent.\n\n"
        f"## Program intent (per component)\n{program_context}\n\n"
    )
    if rag_context:
        user_msg += f"## Reference\n{rag_context}\n\n"
    user_msg += "Output a single main file or a small set of files. For each file, start with a line: FILE: path/to/file.ext then the code. Use plain code blocks."

    try:
        out = await completion(
            request.provider,
            [
                {"role": "system", "content": "You are an expert software engineer. Output only valid code and FILE: headers, no extra commentary."},
                {"role": "user", "content": user_msg},
            ],
            model=request.model,
            api_keys=request.api_keys,
            max_tokens=8192,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    # Parse FILE: path lines and split into artifacts
    artifacts: dict[str, str] = {}
    current_path: str | None = None
    current_lines: list[str] = []
    for line in out.splitlines():
        if line.strip().startswith("FILE:"):
            if current_path is not None and current_lines:
                artifacts[current_path] = "\n".join(current_lines).strip()
            current_path = line.strip()[5:].strip()
            current_lines = []
        elif current_path is not None:
            current_lines.append(line)
    if current_path is not None and current_lines:
        artifacts[current_path] = "\n".join(current_lines).strip()
    if not artifacts and out.strip():
        artifacts["main"] = out.strip()

    # Save to MongoDB (5MB limit)
    user_id = clerk_user_id or request.user_id or ""
    generation_id = None
    if mongo_service.is_available():
        generation_id = mongo_service.save_generated_code(
            user_id=user_id,
            program_id=request.program_id,
            artifacts=artifacts,
        )

    return {
        "artifacts": artifacts,
        "raw": out,
        "generation_id": generation_id,
    }


@router.get("/generated/{generation_id}")
async def get_generated_code(generation_id: str) -> dict:
    """Return generated code by id (for download or view)."""
    if not mongo_service.is_available():
        raise HTTPException(status_code=503, detail="MongoDB is not configured or unavailable.")
    data = mongo_service.get_generated_code(generation_id)
    if data is None:
        raise HTTPException(status_code=404, detail=f"Generation {generation_id} not found.")
    return data


@router.get("/generated")
async def list_generated(
    user_id: str | None = Query(None, description="User id (Clerk) to list generations; optional if authenticated"),
    limit: int = Query(50, ge=1, le=200),
    clerk_user_id: str | None = Depends(get_current_user_optional),
) -> list:
    """List generated code entries for a user. If authenticated, user_id can be omitted (uses Clerk id)."""
    if not mongo_service.is_available():
        raise HTTPException(status_code=503, detail="MongoDB is not configured or unavailable.")
    uid = clerk_user_id or user_id
    if not uid:
        raise HTTPException(status_code=400, detail="user_id required or sign in with Clerk.")
    return mongo_service.list_generated_for_user(uid, limit=limit)
