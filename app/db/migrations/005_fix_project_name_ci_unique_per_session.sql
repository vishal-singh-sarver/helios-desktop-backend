DROP INDEX IF EXISTS idx_projects_name_ci;

CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_session_name_ci
ON projects(session_id, lower(name));

INSERT OR IGNORE INTO schema_migrations(version) VALUES (5);