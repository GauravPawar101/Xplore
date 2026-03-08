-- Chat message persistence for Xplore
-- Stores per-session chat history (user + assistant turns) optionally linked to a codebase graph.

CREATE TABLE IF NOT EXISTS chat_messages (
    id          BIGSERIAL PRIMARY KEY,
    session_id  TEXT        NOT NULL,
    user_id     TEXT        NOT NULL DEFAULT '',
    codebase_id TEXT,
    role        TEXT        NOT NULL,   -- 'user' | 'assistant'
    content     TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS chat_messages_session_idx ON chat_messages (session_id, created_at);
