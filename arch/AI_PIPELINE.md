# AI Pipeline — LLM, Narrator, RAG, and Code Generation

## AI Systems Overview

EzDocs has four distinct AI-powered systems:

| System              | Purpose                                   | LLM Used                                    |
|---------------------|-------------------------------------------|---------------------------------------------|
| Code Explanation    | Explain individual code symbols           | Ollama (local) or HuggingFace Router (cloud)|
| Codebase Narrator   | Linear BFS guided tour of codebases       | Ollama or HuggingFace Router                |
| RAG Search          | Keyword + vector search over code         | HuggingFace Inference (embeddings only)     |
| Code Generation     | Generate code from program intent graphs  | OpenAI / Anthropic / HuggingFace            |

---

## 1. Code Explanation (`shared/ai.py`)

### How It Works

```
Code snippet + context (callers, callees)
       │
       ▼
_build_messages():
  system: "You are a senior developer explaining code..."
  context block: caller/callee graph context (if provided)
  user: code snippet
       │
       ▼
OpenAI-compatible client (streaming):
  Ollama:  POST {OLLAMA_HOST}/v1/chat/completions
  HF:      POST router.huggingface.co/v1/chat/completions
       │
       ▼
Stream chunks back to caller
```

### Provider Priority

1. **Ollama** — Used when `OLLAMA_HOST` is set. Model: `OLLAMA_MODEL` (default: `qwen2.5-coder:3b`).
2. **HuggingFace Router** — Fallback when Ollama is not configured. Model: `HF_MODEL_ID` (default: `Qwen/Qwen3-235B-A22B`).

Both use the `openai` Python SDK as a client against OpenAI-compatible endpoints.

### Key Functions

| Function                       | Description                                          |
|--------------------------------|------------------------------------------------------|
| `generate_explanation_stream`  | Async generator yielding streamed explanation chunks  |
| `generate_explanation`         | Blocking single-shot explanation                      |
| `generate_summary`             | One-line summary for a graph node                     |
| `prefetch_explanations_sync`   | Batched parallel summary generation using ThreadPool  |
| `explain_graph`                | Batch async explain for multiple nodes                |
| `chat_stream`                  | Streaming conversational chat with context            |

### Batch Explanation (Jobs)

When a codebase is analyzed, a `graph_explain` background job generates summaries for all user-code nodes:

```
graph_explain job starts
       │
       ▼
Load all non-library nodes from job result
       │
       ▼
For each node:
  generate_summary({name, type, code})
       │
       ▼
Update each node summary individually in Postgres
  via _update_node_summary()
```

Aborts early if all nodes in a batch fail (indicates Ollama is unresponsive).

---

## 2. Codebase Narrator (`shared/narrator.py`)

### Architecture: Linear BFS Narrator

The active narrator implementation (`shared/narrator.py`) uses a linear BFS walk through graph nodes, streaming explanations via WebSocket.

### Tour Planning

1. **Find entry node** — Highest `entry_score` among all nodes.
2. **Build BFS order** — Starting from the entry node, walk edges breadth-first.
3. **Limit** — Up to 20 nodes in the tour (configurable).

### Functions

| Function            | Description                                              |
|---------------------|----------------------------------------------------------|
| `run_narration`     | Full codebase tour over WebSocket                        |
| `run_node_narration`| Single-node deep-dive narration over WebSocket           |

### WebSocket Protocol

Messages sent from server to client:

```json
{"type": "focus", "node_id": "abc123", "label": "main"}
{"type": "text", "chunk": "This function initializes..."}
{"type": "pause"}
{"type": "done"}
{"type": "error", "message": "..."}
```

Messages sent from client to server:

```json
{"action": "continue"}
{"action": "question", "text": "What does this return?"}
{"action": "focus", "node_id": "def456"}
```

### LangGraph Narrator (`shared/narrator_graph.py`) — Unused

A more advanced state-machine narrator built on LangGraph `StateGraph` with interrupt-based pause exists but is **not currently wired** into any router. `routers/narrator_ws.py` imports `shared.narrator`, not `shared.narrator_graph`.

The LangGraph version has states: `plan_tour` → `explain_node` → `pause` → `route_input` (conditional: continue / question / focus).

### TTS Integration (Frontend)

The narrator output supports TTS on the frontend side:
- **Browser SpeechSynthesis** — Built-in Web Speech API.
- **Kokoro AudioContext** — Higher-quality TTS via AudioContext.

Both operate on a sentence queue with cancellation refs. Two independent TTS pipelines exist in `CodeMap.tsx`:
1. **Tour TTS** — For the full codebase tour.
2. **Node TTS** — For per-node deep-dive narration.

---

## 3. RAG System

### Components

| Component           | File                         | Role                                      |
|---------------------|------------------------------|-------------------------------------------|
| Embedding Generator | `shared/embedding.py`        | Text → vectors via HF Inference API       |
| Vector Store        | `shared/milvus_service.py`   | Milvus ANN storage/search                 |
| Keyword Search      | `shared/db.py`               | Postgres ILIKE search on node columns     |
| Router Endpoints    | `routers/rag.py`             | HTTP API for query and indexing            |

**Note:** `shared/rag_chain.py` (LangChain hybrid retriever + conversational chain) exists but is **not imported** by `routers/rag.py`. The router calls `shared.db` and `shared.milvus_service` directly.

### Indexing Pipeline

```
POST /rag/index { codebase_id }
       │
       ▼
Fetch all graph_nodes from Postgres
  WHERE codebase_id = $1
       │
       ▼
Filter: skip nodes where is_library = true or code is empty
       │
       ▼
For each node (semaphore-bounded concurrency):
  ┌─────────────────────────────────────┐
  │ embed_text(node.name + " " + code)  │
  │   POST api-inference.huggingface.co │
  │   Model: nomic-ai/nomic-embed-text  │
  │   → float vector (384 dims)         │
  └──────────────┬──────────────────────┘
                 │
       Collect all (node_id, vector) pairs
                 │
                 ▼
  milvus_service.insert_embeddings(codebase_id, ids, vectors)
    - Delete existing vectors for this codebase
    - Insert fresh vectors
    - IVF_FLAT index, inner product metric
```

### Query Pipeline

```
POST /rag/query { codebase_id, query, k, use_vector?, program_id? }
       │
       ├── Always: Postgres keyword search
       │   db.rag_query_keyword(codebase_id, query, k, program_id)
       │   ILIKE on name, filepath, code, summary
       │
       ├── If use_vector=true AND Milvus available:
       │   embed_text(query) → query vector
       │   milvus_service.search(codebase_id, query_vector, k)
       │
       ▼
  Merge keyword + vector results
  Deduplicate by node ID
  Return top-k RagChunk list
```

### Embedding Configuration

| Setting             | Default                           | Source                    |
|---------------------|-----------------------------------|---------------------------|
| Model               | `nomic-ai/nomic-embed-text-v1.5`  | `HF_EMBEDDING_MODEL_ID` |
| Dimension           | 384                               | `EMBEDDING_DIM`          |
| Input truncation    | 8,000 chars                       | Hardcoded                |
| API                 | HuggingFace Inference             | `api-inference.huggingface.co` |

---

## 4. Code Generation (`routers/program.py` + `shared/llm_providers.py`)

### Program Graph Concept

Users create "program graphs" — visual intent diagrams where each node represents a desired feature/behavior and edges show dependencies. These are stored in Postgres as JSONB.

### Summarization Pipeline

```
POST /program/summarize { program_id, provider, model?, api_keys? }
       │
       ▼
Load program graph from Postgres
       │
       ▼
For each node:
  LLM prompt: "Summarize this program component
               in 1-2 sentences: {label}: {content}"
       │
       ▼
  llm_providers.completion(provider, messages, model, api_keys)
       │
       ▼
Update each node summary in Postgres (JSONB update)
Return updated program graph
```

### Code Generation Pipeline

```
POST /generate/code {
  program_id, codebase_id?, target_language, stack,
  provider, model?, api_keys?, user_id?
}
       │
       ▼
Load program graph from Postgres
       │
       ▼
(if codebase_id) RAG context retrieval:
  For each node summary → embed → Milvus search → top-k context
       │
       ▼
Build prompt:
  "Generate a complete implementation...
   Target: {target_language} / {stack}
   Program structure:
   - Node 1: {label} - {content}
   ...
   Existing codebase context (if any):
   {RAG results}"
       │
       ▼
llm_providers.completion(provider, messages, model, api_keys)
       │
       ▼
Parse output:
  Split on "FILE: " markers → { "path/file.ext": "code..." }
       │
       ▼
mongo_service.save_generated_code(user_id, program_id, artifacts)
       │
       ▼
Return { generation_id, artifacts }
```

### Multi-Provider LLM Abstraction (`shared/llm_providers.py`)

```
completion(provider, messages, model?, api_keys?)
       │
       ├── provider == "openai"
       │   └── OpenAI async SDK → gpt-4o-mini (default)
       │
       ├── provider == "anthropic"
       │   └── Anthropic async SDK → claude-3-5-haiku-20241022 (default)
       │
       └── provider == "huggingface"
           └── HF Inference API (api-inference.huggingface.co)
               → HF_MODEL_ID (default: Qwen/Qwen3-235B-A22B)
```

**BYOK (Bring Your Own Key):** Users can pass API keys in the request body via `api_keys`. If not provided, falls back to server-side environment variables.

---

## LLM Models Used

| System              | Default Model               | Provider             | Configurable Via        |
|---------------------|-----------------------------|----------------------|-------------------------|
| Code Explanation    | qwen2.5-coder:3b            | Ollama (local)       | `EZDOCS_MODEL`          |
| Narrator            | qwen2.5-coder:3b            | Ollama (local)       | `EZDOCS_MODEL`          |
| Chat                | qwen2.5-coder:3b            | Ollama (local)       | `EZDOCS_MODEL`          |
| Explanation (cloud) | Qwen/Qwen3-235B-A22B        | HuggingFace Router   | `EZDOCS_HF_MODEL`       |
| Embeddings          | nomic-ai/nomic-embed-text-v1.5 | HuggingFace Inference | `EZDOCS_HF_EMBEDDING_MODEL` |
| Summarization       | gpt-4o-mini / claude-3-5-haiku | OpenAI / Anthropic | Per-request `model` param |
| Code Generation     | gpt-4o-mini / claude-3-5-haiku | OpenAI / Anthropic | Per-request `model` param |

Ollama is used for all real-time, streaming operations when available. HuggingFace Router is the cloud fallback. Cloud LLMs (OpenAI/Anthropic) are used for code generation and program summarization where the user selects their provider.
