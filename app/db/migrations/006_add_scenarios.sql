-- Migration 006 — add scenarios table
-- Each project has >=1 scenarios. Each scenario owns its own weather CSV.

CREATE TABLE IF NOT EXISTS scenarios (
    id           TEXT PRIMARY KEY,                             -- UUID string
    project_id   TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    name         TEXT NOT NULL,
    weather_path TEXT,                                         -- nullable: path to weather.csv on disk
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (project_id, name)
);

CREATE INDEX IF NOT EXISTS idx_scenarios_project ON scenarios(project_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (6);
