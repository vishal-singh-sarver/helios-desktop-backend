-- Migration 001 — initial schema
-- Tables: projects, project_versions, project_objects

CREATE TABLE IF NOT EXISTS schema_migrations (
    version     INTEGER PRIMARY KEY,
    applied_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ── Projects ──────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS projects (
    id                  TEXT PRIMARY KEY,          -- UUID string
    name                TEXT NOT NULL,
    latitude            REAL NOT NULL DEFAULT 0.0,
    longitude           REAL NOT NULL DEFAULT 0.0,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    current_version_id  INTEGER REFERENCES project_versions(id) ON DELETE SET NULL
);

-- ── Version snapshots ─────────────────────────────────────────────────────
-- scene_xml   : lzma-compressed Helios XML  (full context snapshot)
-- registry_json: JSON string of _object_registry at that point in time
CREATE TABLE IF NOT EXISTS project_versions (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    version_num      INTEGER NOT NULL,
    label            TEXT,                         -- optional user-provided label
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    scene_xml        BLOB NOT NULL,                -- lzma compressed
    registry_json    TEXT NOT NULL,
    bytes_original   INTEGER NOT NULL DEFAULT 0,
    bytes_compressed INTEGER NOT NULL DEFAULT 0,
    UNIQUE (project_id, version_num)
);

-- ── Object registry ───────────────────────────────────────────────────────
-- Mirrors the in-memory _object_registry (kept in sync on every save).
CREATE TABLE IF NOT EXISTS project_objects (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id      TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    object_id       INTEGER NOT NULL,              -- matches _object_registry key
    name            TEXT NOT NULL,
    type            TEXT NOT NULL,                 -- tile | primitive | tree | import | canopy
    primitive_uuids TEXT NOT NULL DEFAULT '[]',    -- JSON array of ints
    children        TEXT NOT NULL DEFAULT '[]',    -- JSON array of child dicts
    UNIQUE (project_id, object_id)
);

-- ── Indexes ───────────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_versions_project ON project_versions(project_id, version_num DESC);
CREATE INDEX IF NOT EXISTS idx_objects_project  ON project_objects(project_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (1);
