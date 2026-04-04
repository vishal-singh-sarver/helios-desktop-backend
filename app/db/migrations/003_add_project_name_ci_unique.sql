CREATE UNIQUE INDEX IF NOT EXISTS idx_projects_name_ci
ON projects(lower(name));

INSERT OR IGNORE INTO schema_migrations(version) VALUES (3);