"""RAG and graph persistence endpoints."""

import logging

from fastapi import APIRouter, HTTPException, Query

from shared import db, milvus_service
from shared.embedding import embed_text, get_embedding_dim
from shared.schemas import RagChunk, RagQueryRequest, RagQueryResponse

log = logging.getLogger("ezdocs.rag")

router = APIRouter(prefix="/rag", tags=["rag"])


@router.post("/query", response_model=RagQueryResponse)
async def rag_query(request: RagQueryRequest) -> RagQueryResponse:
    """
    RAG retrieval: Postgres (keyword) + optional Milvus (vector).
    Returns top-k chunks (symbols and optionally program nodes) matching the query.
    """
    if not await db.is_available():
        raise HTTPException(
            status_code=503,
            detail="Postgres is not configured or unavailable. Set DATABASE_URL.",
        )

    chunks: list[dict] = []
    seen_ids: set[str] = set()

    # Keyword search (Postgres)
    keyword_chunks = await db.rag_query_keyword(
        codebase_id=request.codebase_id,
        query=request.query,
        k=request.k,
        program_id=request.program_id,
    )
    for c in keyword_chunks:
        cid = c.get("id", "")
        if cid and cid not in seen_ids:
            seen_ids.add(cid)
            chunks.append(c)

    # Vector search (Milvus) if requested and available
    if request.use_vector and milvus_service.is_available() and len(chunks) < request.k:
        try:
            query_embedding = await embed_text(request.query)
            if query_embedding:
                hits = milvus_service.search(
                    request.codebase_id,
                    query_embedding,
                    k=request.k - len(chunks),
                )
                if hits:
                    symbol_ids = [h["symbol_id"] for h in hits if h.get("symbol_id")]
                    if symbol_ids:
                        vector_chunks = await db.get_graph_nodes_by_ids(request.codebase_id, symbol_ids)
                        for c in vector_chunks:
                            cid = c.get("id", "")
                            if cid and cid not in seen_ids:
                                seen_ids.add(cid)
                                chunks.append(c)
        except Exception as e:
            log.debug("Vector RAG failed: %s", e)

    return RagQueryResponse(
        chunks=[RagChunk(**c) for c in chunks[: request.k]],
    )


@router.post("/index")
async def index_codebase_embeddings(
    codebase_id: str = Query(..., description="Codebase to index for vector RAG"),
) -> dict:
    """
    Generate embeddings for all graph nodes of a codebase and store in Milvus.
    Call after analysis (and optionally after summaries are generated) to enable vector RAG.
    """
    if not await db.is_available():
        raise HTTPException(status_code=503, detail="Postgres is not configured or unavailable.")
    if not milvus_service.is_available():
        raise HTTPException(status_code=503, detail="Milvus is not configured or unavailable.")

    graph = await db.read_codebase_graph(codebase_id)
    if not graph or not graph.get("nodes"):
        raise HTTPException(status_code=404, detail=f"No graph found for codebase_id={codebase_id}")

    dim = get_embedding_dim()
    symbol_ids: list[str] = []
    texts: list[str] = []
    for n in graph["nodes"]:
        nid = n.get("id", "")
        data = n.get("data", {})
        name = data.get("label", "")
        code = data.get("code", "") or ""
        summary = data.get("summary", "") or ""
        text = f"{name}\n{summary}\n{code[:2000]}".strip()
        if not text:
            continue
        symbol_ids.append(nid)
        texts.append(text)

    embeddings: list[list[float]] = []
    for t in texts:
        vec = await embed_text(t)
        if vec:
            embeddings.append(vec)
        else:
            embeddings.append([0.0] * dim)
    if len(embeddings) != len(symbol_ids):
        raise HTTPException(status_code=500, detail="Embedding generation failed for some nodes.")

    ok = milvus_service.insert_embeddings(codebase_id, symbol_ids, embeddings, dim=dim)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to insert embeddings into Milvus.")
    return {"codebase_id": codebase_id, "indexed": len(symbol_ids)}
