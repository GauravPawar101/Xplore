"""
EzDocs backend configuration.

All settings are read from environment variables with sensible defaults.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")
load_dotenv(Path(__file__).resolve().parent / ".env")

# ─── API ─────────────────────────────────────────────────────────────────────

API_TITLE = os.getenv("EZDOCS_API_TITLE", "EzDocs API")
API_VERSION = os.getenv("EZDOCS_API_VERSION", "0.2.0")
API_DESCRIPTION = os.getenv(
    "EZDOCS_API_DESCRIPTION",
    "Backend for serving code dependency graphs and AI explanations.",
)

# ─── CORS ────────────────────────────────────────────────────────────────────

# Fixed ports: frontend tries these in order; backend allows exactly these origins (no regex).
FRONTEND_PORTS = (5173, 5174, 5175, 5176, 5177)
_BASE_ORIGINS = [
    f"http://localhost:{p}" for p in FRONTEND_PORTS
] + [
    f"http://127.0.0.1:{p}" for p in FRONTEND_PORTS
]
CORS_ORIGINS = list(_BASE_ORIGINS)
# Only use regex if explicitly set (e.g. production domain); else use fixed ports above.
CORS_ORIGIN_REGEX = os.getenv("EZDOCS_CORS_ORIGIN_REGEX", "").strip() or None
_extra = os.getenv("EZDOCS_CORS_ORIGINS")
if _extra:
    CORS_ORIGINS.extend(origin.strip() for origin in _extra.split(",") if origin.strip())

# ─── Server ───────────────────────────────────────────────────────────────────

HOST = os.getenv("EZDOCS_HOST", "0.0.0.0")
PORT = int(os.getenv("EZDOCS_PORT", "8000"))
RELOAD = os.getenv("EZDOCS_RELOAD", "true").lower() in ("1", "true", "yes")
WS_MAX_SIZE = int(os.getenv("EZDOCS_WS_MAX_SIZE", "10000000"))
# Service ports (fallbacks for microservices; CORS uses FRONTEND_PORTS only)
PORT_GRAPH = int(os.getenv("EZDOCS_PORT_GRAPH", "8001"))
PORT_RAG = int(os.getenv("EZDOCS_PORT_RAG", "8003"))
PORT_PROGRAM = int(os.getenv("EZDOCS_PORT_PROGRAM", "8004"))
PORT_AI = int(os.getenv("EZDOCS_PORT_AI", "8002"))

# ─── Analysis ────────────────────────────────────────────────────────────────

# max_files semantics:
#   0  -> scan full codebase (no hard cap)
#   >0 -> scan up to that many files
DEFAULT_MAX_FILES = int(os.getenv("EZDOCS_MAX_FILES", "0"))
MAX_FILES_CEILING = int(os.getenv("EZDOCS_MAX_FILES_CEILING", "50000"))

# Truncate code in API response to reduce payload (full code still used for edge detection)
MAX_CODE_DISPLAY_LENGTH = int(os.getenv("EZDOCS_MAX_CODE_DISPLAY_LENGTH", "4000"))

# Parallel file parsing (1 = sequential [default], >1 = explicit thread count)
PARSE_WORKERS = int(os.getenv("EZDOCS_PARSE_WORKERS", "1"))

# Prefetch AI explanations for every node during analysis (slow). If False, explanations load on demand when user clicks.
PREFETCH_EXPLANATIONS = os.getenv("EZDOCS_PREFETCH_EXPLANATIONS", "false").lower() in ("true", "1", "yes")

# Parser: skip files larger than this (bytes). Lower = faster analysis, less coverage.
MAX_FILE_SIZE = int(os.getenv("EZDOCS_MAX_FILE_SIZE", str(1024 * 1024)))  # 1 MB

# ─── Postgres (user data, analyses, program graphs, codebase graph) ─────────────

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/ezdocs")

# ─── MongoDB (generated code only; 5MB limit per generation) ───────────────────

MONGODB_URI = os.getenv("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB = os.getenv("MONGODB_DB", "ezdocs")
MONGODB_GENERATED_COLLECTION = os.getenv("MONGODB_GENERATED_COLLECTION", "generated_code")
GENERATED_CODE_MAX_BYTES = int(os.getenv("EZDOCS_GENERATED_CODE_MAX_BYTES", str(5 * 1024 * 1024)))  # 5MB

# ─── Milvus (vector DB for graph embeddings / RAG) ───────────────────────────

MILVUS_URI = os.getenv("MILVUS_URI", "http://localhost:19530")
MILVUS_COLLECTION = os.getenv("MILVUS_COLLECTION", "ezdocs_graph_embeddings")
EMBEDDING_DIM = int(os.getenv("EZDOCS_EMBEDDING_DIM", "384"))  # e.g. all-MiniLM-L6-v2

# ─── LLM providers (user API keys) ────────────────────────────────────────────

# Server-side keys (optional). If set, endpoints can use them when no per-request key is given.
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# Hugging Face Inference API
HUGGINGFACE_TOKEN = os.getenv("HUGGINGFACE_HUB_TOKEN", "").strip() or os.getenv("HF_TOKEN", "").strip()
HUGGINGFACE_TOKEN0 = os.getenv("HUGGINGFACE_HUB_TOKEN0", "").strip() or os.getenv("HF_TOKEN0", "").strip()
HF_MODEL_ID = os.getenv("EZDOCS_HF_MODEL", "Qwen/Qwen3-235B-A22B")
HF_EMBEDDING_MODEL_ID = os.getenv("EZDOCS_HF_EMBEDDING_MODEL", "nomic-ai/nomic-embed-text-v1.5")

# Ollama (local LLM — takes priority over HF when OLLAMA_HOST is set)
OLLAMA_HOST  = os.getenv("OLLAMA_HOST",    "http://127.0.0.1:11434").strip()
OLLAMA_MODEL = os.getenv("EZDOCS_MODEL",   "qwen2.5-coder:3b").strip()

# ─── Ingestion ────────────────────────────────────────────────────────────────

INGEST_DIR = Path(os.getenv("EZDOCS_INGEST_DIR", "./ingested_codebases"))

# ─── Clerk (auth) ────────────────────────────────────────────────────────────

CLERK_JWKS_URL = os.getenv("CLERK_JWKS_URL", "")

# ─── Job queue ───────────────────────────────────────────────────────────────

JOB_RESULT_TTL_SECONDS = int(os.getenv("EZDOCS_JOB_RESULT_TTL_SECONDS", "3600"))  # kept for compat

# ─── Microservices (used by gateway when proxying) ───────────────────────────

GRAPH_SVC_URL = os.getenv("EZDOCS_GRAPH_SVC_URL", f"http://localhost:{PORT_GRAPH}")
AI_SVC_URL = os.getenv("EZDOCS_AI_SVC_URL", f"http://localhost:{PORT_AI}")
RAG_SVC_URL = os.getenv("EZDOCS_RAG_SVC_URL", f"http://localhost:{PORT_RAG}")
PROGRAM_SVC_URL = os.getenv("EZDOCS_PROGRAM_SVC_URL", f"http://localhost:{PORT_PROGRAM}")