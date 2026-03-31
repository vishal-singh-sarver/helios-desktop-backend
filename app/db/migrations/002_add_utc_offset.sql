-- Migration 002 — add utc_offset to projects
-- utc_offset: fractional hours from UTC, e.g. 5.5 for IST, -7.0 for MST
-- Calculated at project creation time from latitude/longitude via timezonefinder.

ALTER TABLE projects ADD COLUMN utc_offset REAL NOT NULL DEFAULT 0.0;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (2);
