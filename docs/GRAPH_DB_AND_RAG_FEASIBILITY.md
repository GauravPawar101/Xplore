# Graph DB + Stored Parse Format + RAG — Feasibility

This document covers: persisting the graph and tree-sitter–shaped data, RAG over the codebase, **a conversational agent** backed by that store, and **a Bicep-like language** for expressing and generating programs in any target language.

## 1. Current state (EzDocs)

| Aspect | Current behavior |
|--------|------------------|
| **Parse format** | Tree-sitter is used only for **extraction**: `UniversalParser` runs language-specific queries and returns a **flat list** of `{ name, type, start_line, end_line, code }` per file. The raw AST is **not** stored. |
| **Graph** | Built in memory (NetworkX). Nodes = symbols (`filepath::name`), edges = heuristic “calls” from tokenising code. After build, `to_json()` produces React Flow–style `{ nodes, edges }`. |
| **Persistence** | **None.** `state.graph_cache` holds the last analysis result; it is lost on restart. No codebase ID or versioning. |
| **Summaries** | `ai.generate_summary()` exists (2–4 sentence plain-text) but is **not** used for storage or narration. Explanations are generated on demand (streaming) when the user clicks “Generate” or “Explain This Node”. |
| **RAG / search** | No vector DB, no embeddings, no semantic search. The only “search” is the file explorer (tree) and graph visibility (expand/focus). |
| **Similar approaches in this repo** | No graph DB or RAG in the main app. `ingest` clones/explodes zip to disk; `ingested_codebases/` is just a file tree (often from other projects). No shared “codebase index” or query layer. |

---

## 2. Goal: graph DB + tree-sitter-shaped storage + RAG

- **Store** in a DB:
  - **Parse output** in a form close to how tree-sitter parses (e.g. per-file AST or at least per-symbol rows with filepath, name, type, line range, code snippet).
  - **Pre-generated summaries** (and optionally embeddings) for each symbol (and maybe file/module).
- **Frontend** asks the backend for “this codebase” or “this view”; **backend queries the DB** and returns graph + metadata; UI displays it.
- **RAG**: natural-language or keyword query → backend queries DB (and optionally vector search) → returns relevant symbols/snippets/summaries for use in an LLM context.
- **Conversational agent** (planned): Multi-turn chat over the codebase; each turn can use RAG + graph queries; the DB is the agent’s long-term context.
- **Bicep-like language** (later): A DSL or IR for describing programs/intent; compiles or generates code in any target language (Python, TypeScript, etc.), using the graph/DB to align generated code with the existing codebase.

---

## 3. Feasibility summary

| Component | Feasibility | Notes |
|-----------|-------------|--------|
| Persist “tree-sitter–shaped” data | ✅ Straightforward | Either store the **current** parse result (name, type, filepath, lines, code) or extend the parser to persist raw AST (e.g. S-exp or JSON). No need to change frontend contract. |
| Persist pre-generated summaries | ✅ Straightforward | Add a `summary` (and optional `embedding`) field per node; backfill via `ai.generate_summary()` during or after graph build. |
| Graph DB for querying | ✅ Feasible, several options | See options below. |
| Frontend → Backend → DB → display | ✅ Feasible | Backend becomes the only reader of the DB; frontend keeps calling `/analyze`, `/graph`, or new endpoints that **read from DB** instead of building the graph on the fly. |
| RAG over stored data | ✅ Feasible | Keyword/structural queries from DB + optional vector search on embeddings; backend exposes e.g. `/rag/query` returning chunks for the LLM. |
| Conversational agent | ✅ Feasible | Agent = LLM + RAG + optional tools (graph query, code fetch). Session/history stored or passed per request; DB is the codebase context. |
| Bicep-like DSL → any language | ✅ Feasible long-term | DSL/IR describes intent or structure; compiler/emitter produces target code; graph DB informs naming, placement, and style. |

---

## 4. Storage options

### A. SQLite + JSON (simplest)

- **Schema**: one table per “codebase” or a single DB with `codebase_id`.
  - **nodes**: `id, codebase_id, filepath, name, type, start_line, end_line, code, summary, embedding BLOB (optional)`.
  - **edges**: `codebase_id, source_id, target_id, edge_type`.
- **Pros**: No extra service, good for single-user / desktop, easy to ship. Tree-sitter “shape” = one row per symbol + JSON column if you later add raw AST.
- **Cons**: Graph traversal (e.g. “all callers of X”) is doable but less natural than a native graph DB; for RAG you’d add FTS5 or an external vector store.

### B. Neo4j (or similar graph DB)

- **Model**: Nodes = symbols/files; relationships = CALLS, CONTAINS, etc. Properties: filepath, name, type, code snippet, summary, optional embedding.
- **Pros**: Natural fit for “show subgraph”, “all paths from entry”, “who calls this”. Cypher is a good fit for RAG context (e.g. “return callers and callees of this function”).
- **Cons**: Extra deployment; need to keep Neo4j in sync when re-analyzing.
- **Chosen**: Neo4j is the target graph store for implementation; program nodes (user intent) and code generation are described in [Neo4j + RAG + Code Generation](NEO4J_RAG_AND_CODE_GEN.md).

### C. Hybrid: SQLite (or Postgres) + vector extension

- **SQLite**: nodes/edges + summaries (and file-level metadata).
- **Vector**: SQLite vec0 / pgvector / or a small embedding service; store embeddings by node (or by chunk). RAG = vector similarity + optional keyword filter.
- **Pros**: One DB for structure and (optionally) vectors; no separate graph engine unless you need heavy traversal.

### D. Keep NetworkX in memory, persist to disk

- **Current** graph builder writes a **JSON snapshot** (e.g. `{ nodes, edges }` plus a `summaries: { node_id: "..." }` map) to a file or blob store keyed by `codebase_id`.
- **On load**: Backend reads the snapshot, reconstructs the graph (or a minimal structure) and serves it; optionally “warm” the in-memory cache.
- **Pros**: Minimal change; format can stay tree-sitter–friendly (e.g. one entry per symbol with line range and code). RAG can be a separate index (e.g. FTS or vector) over the same snapshot.

---

## 5. “Tree-sitter format” in the DB

Today the parser **does not** persist the raw tree. Two levels of “tree-sitter–shaped” storage:

1. **Current extraction as canonical shape**  
   Store exactly what you have now: one record per symbol with `filepath, name, type, start_line, end_line, code`. Optionally add `language`. This is already “tree-sitter–derived” and is enough for graph + RAG.

2. **Full AST**  
   If you need to support arbitrary tree-sitter queries later (e.g. “all `if` conditions in this file”), persist the raw tree:
   - Either **S-expression** from `tree.root_node.sexp()` (compact, tree-sitter–native), or
   - A **JSON** form of the tree (e.g. recursive `{ type, start_byte, end_byte, children }`).
   - Store one blob per file; query with a small engine or by re-parsing on demand. Useful for advanced RAG (e.g. “snippets containing pattern X”).

Recommendation: start with (1); add (2) only if you need richer structural search.

---

## 6. Frontend → Backend → DB flow

- **Codebase identity**: Introduce a stable `codebase_id` (e.g. path hash, or repo URL + commit, or upload ID). All persisted data is keyed by it.
- **Analyze**:  
  - **Option A**: Same as today — build graph in memory, then **also** write to DB (nodes, edges, and optionally run summary generation and store results).  
  - **Option B**: “Load from DB” — if DB has a recent graph for this `codebase_id`, return it and skip full re-parse (optional incremental parse later).
- **New/updated endpoints** (examples):
  - `GET /graph?codebase_id=...` — return graph (and summaries) from DB.
  - `GET /graph/query?codebase_id=...&q=...` — structural or keyword query, return matching nodes/snippets (for UI or RAG).
  - `POST /rag/query` — body: `{ codebase_id, query, k }`; return top-k chunks (symbol + summary + code) for use in an LLM prompt.

Frontend continues to call the backend; it does not talk to the DB directly. No change to how the graph is **displayed** (still React Flow nodes/edges).

---

## 7. RAG usage

- **Stored summaries** (and optionally code snippets) are the main RAG corpus.  
- **Retrieval** can be:
  - **Keyword / FTS**: e.g. “auth”, “login” → nodes whose name/summary/filepath match.
  - **Vector**: embed summaries (and maybe code); user query → embed → nearest neighbours → feed to LLM.
  - **Graph-aware**: e.g. “callers of X” or “symbols in file Y” then summarize; good for “explain this function in context”.
- **Flow**: User or agent sends a natural-language question → backend runs retrieval (DB + optional vector) → builds a context string → sends to Ollama/OpenAI with the question. No change to the existing explain/narrator endpoints except they can **optionally** pull context from the DB instead of only the selected node.

---

## 8. Similar approaches in this project

- **EzDocs core**  
  - **Graph**: In-memory only; no DB.  
  - **Parser**: Tree-sitter for extraction; no AST persistence.  
  - **AI**: On-demand explanation/summary; no stored summaries.  
  - **Ingest**: Produces directories under `ingested_codebases/`; no index or DB of “what’s in this codebase”.

- **`ingested_codebases/`**  
  - Used as **input** to the graph builder (local path). Contents are other repos (e.g. Apache Arrow) or uploaded zips. There is no separate “metadata DB” or RAG index over them; each analysis is independent and not reused.

- **Narrator**  
  - Reads `graph_cache["graph"]` (in-memory). It does not query a DB. Storing summaries in a DB would allow the narrator to **read** precomputed summaries instead of (or in addition to) calling the LLM on the fly.

So today there is **no** graph DB or RAG in the main app; the proposed design would add both in a backward-compatible way (analyze still works; new endpoints and optional “load from DB” path).

---

## 11. Conversational agent

The graph DB and RAG store are intended to back a **conversational agent**: multi-turn chat where the user asks about the codebase, requests refactors, or gives high-level instructions that the agent carries out using retrieved context and (optionally) tools.

### 11.1 Role of the DB and RAG

- **Codebase context**: The agent does not "see" the full repo in the prompt. Instead, each turn (or each relevant turn) runs **retrieval** over the persisted graph/summaries/embeddings so the LLM gets a small, relevant slice (e.g. "these N symbols + summaries + snippets").
- **Consistency**: Because the same store backs both the UI graph and the agent, the agent's answers about structure, callers, and "where is X?" stay aligned with what the user sees in EzDocs.
- **Session and history**: For multi-turn coherence you need either:
  - **Stateless**: Client sends last K messages with each request; backend runs RAG for the latest user message and calls the LLM with full history. No server-side session store.
  - **Stateful**: Backend stores conversation by `session_id` (and optionally `codebase_id`); each turn appends to that thread and RAG runs on the new turn. Enables "remember what we did" and later features (e.g. "apply the refactor we discussed").

### 11.2 Agent flow (per turn)

1. **User message** (and optionally `session_id`, `codebase_id`).
2. **Retrieval**: From the message (and maybe last assistant message), derive a query; run RAG (keyword + optional vector) over the codebase store; get top-k chunks (symbols, summaries, code).
3. **Tools** (optional): Agent can call tools such as "get subgraph", "get file content", "list symbols in file X". These hit the same DB or the file tree.
4. **LLM**: Build a prompt with system role (e.g. "You are an expert assistant for this codebase"), retrieved context, and conversation history; stream or return the assistant reply.
5. **Persist** (if stateful): Append user and assistant messages to the session.

### 11.3 Implications for the feasibility

- **Store design**: The same nodes/edges/summaries (and embeddings) used for "frontend graph" and "RAG endpoint" are the **single source of truth** for the agent. No separate "agent index".
- **Endpoints**: Beyond `POST /rag/query`, you may add:
  - `POST /chat` or `POST /agent/turn`: body = `{ codebase_id, session_id?, messages[], stream? }`; backend runs RAG + LLM, returns assistant message(s).
  - `GET /chat/sessions`, `GET /chat/sessions/:id` if you persist history.
- **Scaling**: For many codebases or long histories, consider capping context size (e.g. last N turns + top-k RAG chunks) so prompts stay within model limits.

---

## 12. Bicep-like language: programs in "any" language

A later goal is a **new language** (DSL or IR), analogous to Bicep for infrastructure: a higher-level way to describe programs or intent that **compiles or generates** code in any target language (Python, TypeScript, Java, etc.). The graph DB and agent support this in two ways: (1) **understanding** the existing codebase so generated code fits in, and (2) **emitting** or **recording** generated artifacts.

### 12.1 Why this affects the feasibility

- **Bicep**: Describes infra resources; compiles to ARM JSON. Single target (ARM), well-defined schema.
- **Your case**: "Programming in any language" suggests either:
  - A **DSL** that describes modules, functions, types, and calls; a **compiler/emitter** produces Python, TS, etc., from that description, or
  - An **IR** (intermediate representation) that the agent or a code-gen pipeline produces, which is then rendered to the desired language using templates or a language-specific backend.

In both cases, the **existing codebase** (and thus the graph DB) is the **world** the language and the agent operate on: naming conventions, file layout, existing symbols, and call patterns should guide where and how new code is generated.

### 12.2 How the graph DB and agent support the language

| Concern | Use of graph DB / agent |
|--------|--------------------------|
| **Where does this go?** | DB stores filepath, module boundaries, and (if you add it) file-level summaries. The emitter can "place" new symbols in the right file or suggest a new file path consistent with the project. |
| **What's already there?** | Query by name or RAG: "symbols like X", "functions in file Y". The DSL or IR can reference "existing function F" so the emitter generates a call to F in the target language. |
| **Style and conventions** | Summaries and code snippets in the store are the training data for "how this codebase looks". The emitter (or the LLM that produces the IR) can be prompted with retrieved examples. |
| **Dependencies and edges** | New "call" or "import" in the DSL should create edges in the graph (or at least be validated against existing nodes). So the store is not only read-only for the agent: you may **write back** new nodes/edges when the user "applies" generated code, or you maintain a "pending changes" layer that becomes real when the user accepts. |

### 12.3 Write-back and artifacts

- **Read-only RAG** is enough for "answer questions" and "suggest code." For "create programs" that the user can apply, you need one of:
  - **Artifact emission**: The agent (or the DSL compiler) outputs **patches** or **new files** (e.g. diff text, or full file content). The backend returns them; the frontend or CLI applies them to the repo. No change to the graph DB until the user runs "analyze" again.
  - **Provisional graph**: The DB (or a separate table) stores "pending" nodes/edges from the DSL/agent; the UI can show "would add these symbols"; on "apply", backend writes files and optionally re-runs the parser to refresh the graph.
  - **Direct write**: The compiler/emitter writes to the repo; a **watch** or "re-analyze" job updates the graph DB. Keeps the DB in sync with disk.

Recommendation: start with **artifact emission** (no DB write from the agent); add **provisional graph** or **re-analyze on apply** once the DSL and agent flow are stable.

### 12.4 Phasing the language

- **Phase A — Agent only**: Conversational agent over the codebase using RAG + tools (no new language). Validates that the store and retrieval are good enough for "explain", "where is", "suggest".
- **Phase B — Structured intent**: Agent (or a form) produces a **structured description** (e.g. JSON) of "add a function F that does X and is called from Y". Backend uses RAG + graph to generate target code in one language (e.g. Python). Still no general "language"; just a fixed schema for "add function" / "add module".
- **Phase C — DSL/IR**: Design a small language (syntax or IR) that can express modules, functions, types, and calls; implement a compiler/emitter that targets one or more languages. Use the graph DB to resolve "existing symbol X" and to decide placement and style.
- **Phase D — Multi-target and polish**: Extend the emitter to more languages; integrate with the conversational agent ("create a new API endpoint that validates the config" → agent produces DSL/IR → emitter produces Python FastAPI or TS Express, etc.).

---

## 9. Recommended phases

| Phase | What | Outcome |
|-------|------|--------|
| **1. Persist graph + parse shape** | After each analysis, write to SQLite (or JSON file keyed by `codebase_id`): nodes (filepath, name, type, lines, code), edges (source, target). Optional: store raw AST per file (S-exp or JSON). | Backend can **load** a graph from DB/file instead of re-parsing; restarts don’t lose last analysis. |
| **2. Persist summaries** | During or after graph build, call `ai.generate_summary()` per node (with concurrency limit); store `summary` (and optionally `embedding`) in the same store. | Narrator and Explain can use precomputed summaries; RAG has a text corpus. |
| **3. Query API** | Add `GET /graph?codebase_id=...`, and optionally `GET /graph/query?codebase_id=...&q=...` (keyword/structural). Frontend uses these when “loading” a codebase. | Frontend always queries backend; backend reads from DB. |
| **4. RAG endpoint** | Add `POST /rag/query`: codebase_id + natural-language query; backend retrieves (keyword + optional vector) and returns chunks; optionally a wrapper that calls Ollama with retrieved context. | Same store powers "easy RAG" and later the agent. |
| **5. Conversational agent** | Add `POST /chat` (or `/agent/turn`) with optional session; per turn: RAG over codebase + conversation history → LLM; optional tools (graph query, get file). Persist session/history if stateful. | Multi-turn chat over the codebase with consistent context from the DB. |
| **6. (Optional) Vector search** | Add embeddings for summaries/code; store in SQLite (vec0) or pgvector. RAG and agent use vector similarity. | Better semantic retrieval for the agent and RAG. |
| **7. (Optional) Native graph DB** | Move nodes/edges to Neo4j for complex traversals; keep summaries/embeddings in SQLite or in Neo4j. | Richer graph tools for the agent (e.g. "impact of changing X"). |
| **8. Bicep-like language (later)** | Introduce a DSL or IR for "add module/function/call"; emitter generates code in one or more target languages; use graph DB for placement and style. Start with artifact emission (diffs/new files); optional provisional graph or re-analyze on apply. | Users (or the agent) describe intent in a small language; backend generates code that fits the existing codebase. |

---

## 10. Summary

- **Feasibility**: Storing a tree-sitter–derived shape (symbols + code + line ranges) and pre-generated summaries in a DB is straightforward. Using that for “frontend queries backend → backend queries DB → display” is feasible with no change to how the UI renders the graph.
- **RAG**: The same store (plus optional embeddings) can back an RAG endpoint so you can query the codebase in natural language and feed results into an LLM.
- **Conversational agent**: The same store backs multi-turn chat; each turn can use RAG + optional tools (graph query, get file). Session/history can be stateless (client sends last K messages) or stateful (backend stores by session_id). Phased after RAG (Phase 5).
- **Bicep-like language**: A later DSL or IR for describing programs/intent that compiles or generates code in any target language; the graph DB informs placement, style, and existing symbols. Start with artifact emission (patches/new files); optional provisional graph or re-analyze on apply. Phased after the agent (Phase 8).
- **Existing codebase**: There are no graph DB or RAG implementations in the main EzDocs app today; `ingested_codebases/` is just file trees. This design adds a clear, phased path to both, plus the agent and the DSL.

A practical start is **Phase 1 + 2** with SQLite (or a single JSON file per codebase) and optional **Phase 4** for a simple keyword-based RAG without vectors.
