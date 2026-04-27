-- Migration 008 — weather metadata catalog + per-scenario header mapping.
--
-- Three tables in dependency order:
--   1. helios_data_types       — global catalog of measurement types ("Temperature", "Humidity")
--   2. data_units              — units per type ("Celsius" → Temperature; "%" → Humidity)
--   3. weather_data_headers    — per-scenario mapping: which CSV column = which (data_type, unit)
--
-- Cascade strategy:
--   Project → Scenario → Header        = CASCADE (delete a project, headers go too)
--   DataType → DataUnit                = CASCADE (delete a type, its units go)
--   DataType / DataUnit → Header       = RESTRICT (can't delete a type/unit that's still in use)
--
-- The unit/type consistency invariant (data_units.data_type_id == headers.helios_data_type_id)
-- is enforced at the service layer, not the schema. SQLite can't express it as a CHECK.

CREATE TABLE IF NOT EXISTS helios_data_types (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    data_type   TEXT NOT NULL UNIQUE,
    description TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS data_units (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    unit         TEXT NOT NULL,
    alias        TEXT,
    data_type_id INTEGER NOT NULL REFERENCES helios_data_types(id) ON DELETE CASCADE,
    min          REAL,                                    -- nullable: no validation rule
    max          REAL,                                    -- nullable: no validation rule
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (data_type_id, unit)
);

CREATE INDEX IF NOT EXISTS idx_data_units_data_type ON data_units(data_type_id);

CREATE TABLE IF NOT EXISTS weather_data_headers (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id         TEXT    NOT NULL REFERENCES scenarios(id)         ON DELETE CASCADE,
    helios_data_type_id INTEGER NOT NULL REFERENCES helios_data_types(id) ON DELETE RESTRICT,
    unit_id             INTEGER NOT NULL REFERENCES data_units(id)        ON DELETE RESTRICT,
    name                TEXT    NOT NULL,
    status              INTEGER NOT NULL DEFAULT 1,
    display_order       INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT    NOT NULL DEFAULT (datetime('now')),
    UNIQUE (scenario_id, name)
);

CREATE INDEX IF NOT EXISTS idx_weather_data_headers_scenario ON weather_data_headers(scenario_id);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (8);
