"""
Embedding generation for graph nodes (RAG). Uses Hugging Face Inference API; dimension must match Milvus collection.
"""

import logging
from typing import Optional

from shared.config import EMBEDDING_DIM, HUGGINGFACE_TOKEN, HUGGINGFACE_TOKEN0, HF_EMBEDDING_MODEL_ID

log = logging.getLogger("ezdocs.embedding")
c = 0
HF_TOKEN = HUGGINGFACE_TOKEN if c % 2 == 0 else HUGGINGFACE_TOKEN0
c += 1

def get_embedding_dim() -> int:
    return EMBEDDING_DIM


def _ensure_dim(vec: list[float], dim: int) -> list[float]:
    if len(vec) >= dim:
        return vec[:dim]
    return vec + [0.0] * (dim - len(vec))


async def embed_text(text: str, *, api_key: Optional[str] = None) -> Optional[list[float]]:
    text = (text or "").strip()[:8000]
    if not text:
        return None
    token = api_key or HF_TOKEN
    if not token:
        log.warning("No Hugging Face token available for embedding. Set HUGGINGFACE_HUB_TOKEN.")
        return None
    try:
        import httpx
        url = f"https://api-inference.huggingface.co/models/{HF_EMBEDDING_MODEL_ID}"
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {token}"},
                json={"inputs": text},
            )
            if r.status_code == 503:
                log.warning("HF embedding model is loading, retry shortly.")
                return None
            r.raise_for_status()
            data = r.json()
            # Feature extraction returns a list of floats or list-of-lists
            if isinstance(data, list):
                vec = data[0] if isinstance(data[0], list) else data
                return _ensure_dim([float(v) for v in vec], EMBEDDING_DIM)
    except Exception as e:
        log.debug("HF embed failed: %s", e)
    return None