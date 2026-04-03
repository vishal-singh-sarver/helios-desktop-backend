ALTER TABLE projects ADD COLUMN session_id TEXT NOT NULL DEFAULT '';

CREATE INDEX IF NOT EXISTS idx_projects_session_id ON projects(session_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (4);