-- Migration 010 — add unit-conversion fields to data_units.
--
-- Each unit can now describe how to convert a value into the type's
-- canonical/base unit:
--   value_in_base = value * to_base_factor + to_base_offset
--
-- For example, with Celsius as the base for Temperature:
--   Celsius      → factor=1.0,        offset=0.0,    is_base=1
--   Fahrenheit   → factor=5.0/9.0,    offset=-160/9, is_base=0
--   Kelvin       → factor=1.0,        offset=-273.15, is_base=0
--
-- Defaults are chosen so existing rows become "factor 1, offset 0,
-- not the base" — the no-op affine transform — until each data type's
-- base unit is explicitly designated.
--
-- The partial unique index enforces that each data_type has at most one
-- base unit (the WHERE clause means rows with is_base=0 don't participate).

ALTER TABLE data_units ADD COLUMN to_base_factor REAL    NOT NULL DEFAULT 1.0;
ALTER TABLE data_units ADD COLUMN to_base_offset REAL    NOT NULL DEFAULT 0.0;
ALTER TABLE data_units ADD COLUMN is_base        INTEGER NOT NULL DEFAULT 0;

CREATE UNIQUE INDEX idx_data_units_one_base
    ON data_units(data_type_id)
    WHERE is_base = 1;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (10);
