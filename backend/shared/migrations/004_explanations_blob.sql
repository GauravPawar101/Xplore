CREATE TABLE IF NOT EXISTS codebase_explanations (
    codebase_id TEXT PRIMARY KEY,
    content TEXT NOT NULL DEFAULT ''
);

ALTER TABLE graph_nodes
    ADD COLUMN IF NOT EXISTS explanation_line INT,
    ADD COLUMN IF NOT EXISTS explanation_col INT,
    ADD COLUMN IF NOT EXISTS explanation_offset INT,
    ADD COLUMN IF NOT EXISTS explanation_length INT;
