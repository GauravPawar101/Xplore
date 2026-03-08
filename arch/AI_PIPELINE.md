# AI Pipeline — LLM, Narrator, RAG, and Code Generation

## AI Systems Overview

Xplore has four distinct AI-powered systems:

| System              | Purpose                                   | LLM Used                    |
|---------------------|-------------------------------------------|-----------------------------|
| Code Explanation    | Explain individual code symbols           | Ollama (local)              |
| Codebase Narrator   | Interactive guided tour of codebases      | Ollama via LangGraph        |
| RAG Chat            | Conversational search over code           | Ollama via LangChain        |
| Code Generation     | Generate code from program intent graphs  | OpenAI / Anthropic / HF    |

---

## 1. Code Explanation (`shared/ai.py`)

### How It Works

```
Code snippet + language
       │
       ▼
Build chat messages:
  system: "You are a code explanation assistant..."
  user: "Explain this {language} code:\n{code}"
       │
       ▼
POST /api/chat to Ollama
  model: qwen2.5-coder:8b (configurable via HF_MODEL_ID)
  stream: true
       │
       ▼
Stream chunks back to caller
```

### Key Functions

| Function                      | Description                                    |
|-------------------------------|------------------------------------------------|
| `_hf_chat_stream(messages)`   | Streams chat completion from Ollama `/api/chat`|
| `generate_summary(node)`      | One-line summary for a graph node              |
| `explain_code(code, lang)`    | Full explanation of a code snippet             |

### Batch Explanation (Jobs)

When a codebase is analyzed, a `graph_explain` background job generates summaries for all user-code nodes:

```
graph_explain job starts
       │
       ▼
Load all non-library nodes
       │
       ▼
For each node:
  generate_summary({name, type, code})
       │
       ▼
Batch-write all summaries to Postgres
  (one transaction via batch_update_summaries)
```

Aborts early if all nodes in a batch fail (indicates Ollama is unresponsive).

---

## 2. Codebase Narrator

### Architecture: LangGraph State Machine (`shared/narrator_graph.py`)

The narrator is built as a **LangGraph `StateGraph`** with an interrupt-based pause mechanism for interactivity.

### State Schema

```python
class NarrationState(TypedDict):
    nodes: list          # All graph nodes
    edges: list          # All graph edges
    tour_nodes: list     # Ordered list of nodes to narrate
    current_index: int   # Position in tour_nodes
    user_message: str    # Latest user input from WebSocket
```

### State Machine Diagram

```
                    ┌─────────────┐
                    │  plan_tour  │   Build BFS-ordered tour_nodes
                    └──────┬──────┘   from entry point
                           │
                           ▼
                ┌──────────────────┐
           ┌───▶│  explain_node    │   Send focus frame + stream
           │    │                  │   LLM explanation via WebSocket
           │    └────────┬─────────┘
           │             │
           │             ▼
           │    ┌──────────────────┐
           │    │     pause        │   interrupt() — wait for
           │    │                  │   user WebSocket message
           │    └────────┬─────────┘
           │             │
           │             ▼
           │    ┌──────────────────┐
           │    │  route_input     │   Parse user_message:
           │    │  (conditional)   │   "continue" | question | focus
           │    └───┬────┬────┬───┘
           │        │    │    │
           │   continue  │   focus:{node_id}
           │        │    │    │
           │        ▼    │    ▼
           │   ┌────────┐│  ┌────────────────┐
           │   │advance ││  │ change_focus    │   Jump to
           │   │ idx+1  ││  │ insert node,   │   requested node
           │   └───┬────┘│  │ update index   │
           │       │     │  └───────┬────────┘
           │       │     │          │
           └───────┘     │          └──────────▶ explain_node
                         │
                    question text
                         │
                         ▼
                ┌──────────────────┐
                │ answer_question  │   LLM answers scoped to
                │                  │   current node's code context
                └────────┬─────────┘
                         │
                         ▼
                      advance → explain_node (loop)
```

### Tour Planning (`plan_tour_node`)

Uses the same algorithm as the legacy narrator:

1. **Find entry node** — Highest `entry_score`, with heuristic fallback (outdegree - indegree + name bonus).
2. **Order entry file nodes** — Nodes in the entry file sorted by orchestrator-first (high out, low in, then line number).
3. **BFS walk** — From entry file, visit cross-file nodes breadth-first.
4. **Limit** — Up to 20 nodes in the tour.

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

### TTS Integration (Frontend)

The narrator output supports two TTS backends:
- **Browser SpeechSynthesis** — Built-in Web Speech API
- **Kokoro AudioContext** — Higher-quality TTS via AudioContext

Both operate on a sentence queue with cancellation refs. Two independent TTS pipelines exist:
1. **Tour TTS** — For the full codebase tour
2. **Node TTS** — For per-node deep-dive narration

---

## 3. RAG System

### Components

| Component           | File                    | Role                              |
|---------------------|-------------------------|-----------------------------------|
| Embedding Generator | `shared/embedding.py`   | Text → 768-dim vectors via Ollama |
| Vector Store        | `shared/milvus_service.py` | Milvus ANN storage/search      |
| Keyword Search      | `shared/db.py`          | Postgres pg_trgm ILIKE search    |
| Hybrid Retriever    | `shared/rag_chain.py`   | Combines keyword + vector search |
| Chat Chain          | `shared/rag_chain.py`   | Conversational RAG with history  |
| Router Endpoints    | `routers/rag.py`        | HTTP API for query and indexing  |

### Indexing Pipeline

```
POST /rag/index { codebase_id }
       │
       ▼
Fetch all non-library graph_nodes from Postgres
  WHERE analysis_id = codebase_id AND is_library = false
       │
       ▼
For each node (semaphore-bounded concurrency):
  ┌─────────────────────────────────┐
  │ embed_text(node.name + node.code) │
  │   POST /api/embeddings → Ollama  │
  │   Model: nomic-embed-text        │
  │   → 768-dim float vector         │
  └──────────────┬──────────────────┘
                 │
       Collect all (node_id, vector) pairs
                 │
                 ▼
  milvus_service.insert_embeddings(codebase_id, ids, vectors)
    - Delete existing vectors for this codebase
    - Insert fresh vectors
    - IVF_FLAT index with inner product metric
```

### Hybrid Retrieval

```python
class CodebaseRetriever(BaseRetriever):
    """Combines Postgres keyword + Milvus vector search."""
```

Query processing:

```
User query: "how does authentication work?"
       │
       ├──────────────────┐
       ▼                  ▼
┌──────────────┐   ┌──────────────┐
│ Postgres     │   │ Milvus       │
│ db.rag_query │   │ embed_text   │
│ _keyword()   │   │ (query) →    │
│              │   │ milvus.search│
│ ILIKE on     │   │ ANN top-k    │
│ name, code   │   │              │
└──────┬───────┘   └──────┬───────┘
       │                  │
       └──────┬───────────┘
              ▼
    Deduplicate by chunk ID
    (keyword results + vector results)
              │
              ▼
       Return combined top-k chunks
```

### Conversational Chat Chain

```python
build_chat_chain() → RunnableWithMessageHistory
```

```
Chat prompt template:
  system: "You are a codebase assistant. Use the following context
           to answer the user's question about the code.
           Context: {context}"
  human: "{input}"
       │
       ▼
ChatOllama (local LLM)
       │
       ▼
StrOutputParser
       │
       ▼
Response (with session history via LRU cache)
```

Session management:
- `_LRUSessionStore` — Bounded `OrderedDict` (default max 100 sessions)
- Each session stores `InMemoryChatMessageHistory`
- Old sessions evicted LRU-style when limit exceeded

---

## 4. Code Generation (`routers/program.py`)

### Program Graph Concept

Users create "program graphs" — visual intent diagrams where each node represents a desired feature/behavior and edges show dependencies.

```json
{
  "nodes": [
    {"id": "1", "label": "User Auth", "description": "JWT-based login/signup"},
    {"id": "2", "label": "API Routes", "description": "REST endpoints for CRUD"},
    {"id": "3", "label": "Database", "description": "PostgreSQL with SQLAlchemy"}
  ],
  "edges": [
    {"source": "2", "target": "1"},
    {"source": "2", "target": "3"}
  ]
}
```

### Summarization Pipeline

```
POST /program/summarize { nodes, provider, api_keys }
       │
       ▼
For each node:
  LLM prompt: "Summarize this program component
               in 1-2 sentences: {label}: {description}"
       │
       ▼
  llm_providers.completion(provider, messages, model, api_keys)
       │
       ▼
Return nodes with .summary field populated
```

### Code Generation Pipeline

```
POST /generate/code {
  program_nodes,
  program_edges,
  codebase_id?,      ← optional: reference existing codebase
  provider,
  model?,
  api_keys
}
       │
       ▼
(if codebase_id) RAG retrieval for context:
  search(codebase_id, embed(summary), k=5) per node
       │
       ▼
Build prompt:
  "Generate a complete implementation for this program.
   Program structure:
   - Node 1: {label} - {summary} [depends on: Node 2, Node 3]
   - Node 2: ...

   Existing codebase context (optional):
   {RAG results}

   Output format: FILE: path/to/file.ext followed by code"
       │
       ▼
llm_providers.completion(provider, messages, model)
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
completion(provider, messages, model, api_keys)
       │
       ├── provider == "openai"
       │   └── OpenAI SDK → gpt-4o-mini (default)
       │
       ├── provider == "anthropic"
       │   └── Anthropic SDK → claude-3-5-haiku-20241022 (default)
       │
       └── provider == "huggingface"
           └── HF Inference API → configured HF_MODEL_ID
```

**BYOK (Bring Your Own Key):** Users can pass API keys in the request body via `api_keys`. If not provided, falls back to server-side environment variables. For HuggingFace, uses `token_manager.get_token()` for round-robin rotation across multiple tokens.

---

## LLM Models Used

| System              | Model                    | Provider        | Purpose                  |
|---------------------|--------------------------|-----------------|--------------------------|
| Code Explanation    | qwen2.5-coder:8b        | Ollama (local)  | Explain code snippets    |
| Narrator            | qwen2.5-coder:8b        | Ollama (local)  | Tour narration           |
| RAG Chat            | qwen2.5-coder:8b        | Ollama (local)  | Conversational answers   |
| Embeddings          | nomic-embed-text         | Ollama (local)  | 768-dim text embeddings  |
| Summarization       | gpt-4o-mini / claude-3-5-haiku | OpenAI/Anthropic | Program node summaries |
| Code Generation     | gpt-4o-mini / claude-3-5-haiku | OpenAI/Anthropic | Full code generation   |

Local LLM (Ollama) is used for all real-time, streaming operations. Cloud LLMs are used for batch summarization and code generation where quality matters more than latency.
