"""
Pydantic request/response models for EzDocs API.
"""

from typing import Optional

from pydantic import BaseModel, field_validator


class ExplainRequest(BaseModel):
    """Request body for one-shot code explanation."""

    code: str
    context: str = ""
    callers: Optional[list[str]] = None  # names of functions that call this one
    callees: Optional[list[str]] = None   # names of functions this one calls

    @field_validator("code")
    @classmethod
    def code_must_not_be_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("code must not be empty")
        return v


class GithubRequest(BaseModel):
    """Request body for GitHub repository analysis."""

    url: str

    @field_validator("url")
    @classmethod
    def url_must_look_like_github(cls, v: str) -> str:
        v = v.strip()
        if not (v.startswith("https://github.com/") or v.startswith("github.com/")):
            raise ValueError("url must point to a GitHub repository")
        return v


# ─── Postgres + RAG + Program + Code gen ──────────────────────────────────────

class ApiKeysBody(BaseModel):
    """Optional per-request API keys (bring your own key)."""

    openai: Optional[str] = None
    anthropic: Optional[str] = None


class RagQueryRequest(BaseModel):
    """Request for RAG retrieval over the graph DB."""

    codebase_id: str
    query: str
    k: int = 10
    program_id: Optional[str] = None
    use_vector: bool = True


class RagChunk(BaseModel):
    """Single chunk returned by RAG (symbol or program node)."""

    id: str
    type: str  # "symbol" | "program_node"
    name: Optional[str] = None
    filepath: Optional[str] = None
    summary: Optional[str] = None
    code: Optional[str] = None
    content: Optional[str] = None


class RagQueryResponse(BaseModel):
    """Response of POST /rag/query."""

    chunks: list[RagChunk]


class ProgramNodeInput(BaseModel):
    """One node in a user-defined program graph."""

    id: str
    content: str
    label: Optional[str] = None
    order: Optional[int] = None


class ProgramEdgeInput(BaseModel):
    """Edge between program nodes."""

    source_id: str
    target_id: str


class ProgramGraphRequest(BaseModel):
    """Create or replace a program graph."""

    program_id: str
    nodes: list[ProgramNodeInput]
    edges: list[ProgramEdgeInput] = []
    user_id: Optional[str] = None


class ProgramSummarizeRequest(BaseModel):
    """Request to summarize all program node contents via LLM."""

    program_id: str
    provider: str  # "ollama" | "openai" | "anthropic"
    model: Optional[str] = None
    api_keys: Optional[ApiKeysBody] = None


class JobAnalyzeRequest(BaseModel):
    """Request body for POST /jobs/analyze (Option C queue)."""

    path: Optional[str] = None
    url: Optional[str] = None
    max_files: int = 0
    codebase_id: Optional[str] = None
    user_id: Optional[str] = None


class GenerateCodeRequest(BaseModel):
    """Request to generate code/project from program graph + optional RAG."""

    program_id: str
    codebase_id: Optional[str] = None
    target_language: str  # e.g. "python", "typescript"
    stack: Optional[str] = None  # e.g. "fastapi", "express"
    provider: str
    model: Optional[str] = None
    api_keys: Optional[ApiKeysBody] = None
    user_id: Optional[str] = None
