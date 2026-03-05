-- EzDocs Postgres schema: users, analyses, codebase graph, program graphs

-- Users (Clerk id; optional sync from Clerk webhook)
CREATE TABLE IF NOT EXISTS users (
    id BIGSERIAL PRIMARY KEY,
    clerk_id TEXT NOT NULL UNIQUE,
    email TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Analyses: one per codebase, linked to user
CREATE TABLE IF NOT EXISTS analyses (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    codebase_id TEXT NOT NULL UNIQUE,
    source_path TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Codebase graph: nodes (symbols) for RAG and display
CREATE TABLE IF NOT EXISTS graph_nodes (
    codebase_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    name TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'function',
    filepath TEXT NOT NULL DEFAULT '',
    start_line INT NOT NULL DEFAULT 0,
    end_line INT NOT NULL DEFAULT 0,
    code TEXT NOT NULL DEFAULT '',
    summary TEXT,
    PRIMARY KEY (codebase_id, node_id)
);

CREATE TABLE IF NOT EXISTS graph_edges (
    codebase_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'CALLS',
    PRIMARY KEY (codebase_id, source_id, target_id)
);

CREATE INDEX IF NOT EXISTS idx_graph_nodes_codebase ON graph_nodes(codebase_id);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_name ON graph_nodes(name);
CREATE INDEX IF NOT EXISTS idx_graph_edges_codebase ON graph_edges(codebase_id);

-- Program graphs (user-defined intent)
CREATE TABLE IF NOT EXISTS program_graphs (
    id BIGSERIAL PRIMARY KEY,
    user_id TEXT NOT NULL,
    program_id TEXT NOT NULL UNIQUE,
    name TEXT,
    nodes JSONB NOT NULL DEFAULT '[]',
    edges JSONB NOT NULL DEFAULT '[]',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_program_graphs_user ON program_graphs(user_id);
