-- Add entry_score to graph_nodes for entrypoint detection in DB-loaded graphs
ALTER TABLE graph_nodes ADD COLUMN IF NOT EXISTS entry_score INT NOT NULL DEFAULT 0;
