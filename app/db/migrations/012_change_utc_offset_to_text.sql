-- Migration 012 — change projects.utc_offset from REAL (fractional hours)
-- to TEXT (ISO 8601 offset, e.g. "+05:30", "-07:00").
--
-- Why: matches the offset suffix that datetime.isoformat() already emits
-- in stored timestamps, so frontend can parse/format consistently. Also
-- preserves half-hour and quarter-hour zones (India, Nepal, Chatham, ...)
-- without ambiguity in float-to-string coercion.
--
-- SQLite has no ALTER COLUMN type-change, so the standard rebuild
-- pattern: create _new with TEXT column, copy + convert, drop, rename.
--
-- The conversion math:
--   sign    = "+" if utc_offset >= 0 else "-"
--   hours   = floor(abs(utc_offset))
--   minutes = round((abs(utc_offset) - hours) * 60)
--   result  = sprintf("%s%02d:%02d", sign, hours, minutes)
--
-- Examples produced:
--   5.5  -> "+05:30"
--   5.75 -> "+05:45"
--   0.0  -> "+00:00"
--   -7.0 -> "-07:00"
--   -3.5 -> "-03:30"
--
-- Other tables (scenarios, project_versions, project_objects) carry FKs
-- to projects(id). To allow DROP+RENAME within a single transaction we
-- defer FK checks until commit, by which point projects has been
-- recreated and every FK still resolves.

PRAGMA defer_foreign_keys=ON;

CREATE TABLE projects_new (
    id                 TEXT PRIMARY KEY,
    session_id         TEXT NOT NULL,
    name               TEXT NOT NULL,
    latitude           REAL NOT NULL DEFAULT 0.0,
    longitude          REAL NOT NULL DEFAULT 0.0,
    utc_offset         TEXT NOT NULL DEFAULT '+00:00',
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    current_version_id INTEGER REFERENCES project_versions(id) ON DELETE SET NULL
);

INSERT INTO projects_new
    (id, session_id, name, latitude, longitude, utc_offset, created_at, updated_at, current_version_id)
SELECT
    id, session_id, name, latitude, longitude,
    printf(
        '%s%02d:%02d',
        CASE WHEN utc_offset >= 0 THEN '+' ELSE '-' END,
        CAST(ABS(utc_offset) AS INTEGER),
        CAST(ROUND((ABS(utc_offset) - CAST(ABS(utc_offset) AS INTEGER)) * 60) AS INTEGER)
    ),
    created_at, updated_at, current_version_id
FROM projects;

DROP TABLE projects;

ALTER TABLE projects_new RENAME TO projects;

CREATE INDEX IF NOT EXISTS idx_projects_session_id ON projects(session_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (12);
