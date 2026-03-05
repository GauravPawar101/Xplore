-- Migration 002: add is_library flag to graph_nodes
-- Run once: psql $DATABASE_URL -f shared/migrations/002_library_nodes.sql

ALTER TABLE graph_nodes
    ADD COLUMN IF NOT EXISTS is_library BOOLEAN NOT NULL DEFAULT FALSE;

-- Mark existing library blob nodes (inserted with type='library')
UPDATE graph_nodes SET is_library = TRUE WHERE type = 'library';

CREATE INDEX IF NOT EXISTS idx_graph_nodes_library ON graph_nodes(codebase_id, is_library);