-- Migration 007 — align scenarios columns with the pseudocode:
--   weather_path          →  weather_file_path
--   (new)                 →  context_file_path

ALTER TABLE scenarios RENAME COLUMN weather_path TO weather_file_path;

ALTER TABLE scenarios ADD COLUMN context_file_path TEXT;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (7);
