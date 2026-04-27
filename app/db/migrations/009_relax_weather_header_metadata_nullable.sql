-- Migration 009 — relax NOT NULL on weather_data_headers metadata FKs.
--
-- A column header may now be created without an attached catalog entry:
-- the frontend sometimes adds a column before the user has chosen which
-- (data_type, unit) it represents. Both FKs are therefore nullable.
--
-- SQLite has no ALTER COLUMN to drop a NOT NULL constraint — rebuild
-- the table. No table references weather_data_headers, so the rebuild
-- is safe to do inside the migration's enclosing transaction.

CREATE TABLE weather_data_headers_new (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         TEXT    NOT NULL REFERENCES scenarios(id)         ON DELETE CASCADE,
    helios_data_type_id INTEGER          REFERENCES helios_data_types(id) ON DELETE RESTRICT,
    unit_id             INTEGER          REFERENCES data_units(id)        ON DELETE RESTRICT,
    name                TEXT    NOT NULL,
    status              INTEGER NOT NULL DEFAULT 1,
    display_order       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (scenario_id, name)
);

INSERT INTO weather_data_headers_new
    (id, scenario_id, helios_data_type_id, unit_id, name, status, display_order, created_at, updated_at)
SELECT
    id, scenario_id, helios_data_type_id, unit_id, name, status, display_order, created_at, updated_at
FROM weather_data_headers;

DROP TABLE weather_data_headers;

ALTER TABLE weather_data_headers_new RENAME TO weather_data_headers;

CREATE INDEX IF NOT EXISTS idx_weather_data_headers_scenario ON weather_data_headers(scenario_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (9);
