-- Migration 019 — seed the date_time data type and its format units.
--
-- A single new data_type, 'date_time', whose "units" are not numeric
-- units in the conventional sense — they're pattern strings describing
-- the layout of combined date+time stamps seen in real-world CSV
-- uploads (ISO-8601, EU/US slash and dash variants, etc.).
--
-- Scope: catalog-only. No parsing-flow change. The follow-up task is
-- wiring the chosen format into _transform_csv / loadTabularTimeseriesData
-- so the user-selected format drives parsing instead of auto-sniffing.
--
-- Columns that don't apply to format strings are left at NULL or defaults:
--   - min, max                              → NULL (no numeric range)
--   - to_base_factor, to_base_offset        → default (1.0, 0.0; unused)
--
-- Base format: MM/DD/YYYY HH:MM (US slash 24-hour) — the canonical/default
-- representation everything else converts to.
--
-- Excluded:
--   - Date-only and Time-only format groups (the doc's separate sections);
--     this data type covers *combined* datetime patterns only.
--   - YYYY DOY / DOY YYYY day-of-year formats — flagged as uncommon by
--     the source doc and not worth the extra parsing complexity yet.
--
-- Out of scope here (future cleanup):
--   helios_data_types.parameter_type exists on the live DB with default
--   'linear_factor' but is not currently tracked in any migration file
--   and is not read by any backend code. The new 'date_time' row will
--   inherit 'linear_factor' which is semantically wrong for format
--   strings, but harmless until someone wires parameter_type into
--   behavior. Track the column properly + override it for this row in
--   the same migration that introduces the wiring.

INSERT OR IGNORE INTO helios_data_types (data_type, description) VALUES
    ('date_time',
     'Combined date+time stamp. "Units" are format patterns, not numeric conversions.');

-- ── 8 combined-datetime format patterns ─────────────────────────────────
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, is_base) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'MM/DD/YYYY HH:MM',          'US slash 24-hour',     1),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'YYYY-MM-DDTHH:MM:SSZ',      'ISO-8601 UTC',         0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'YYYY-MM-DDTHH:MM:SS-HH:MM', 'ISO-8601 with offset', 0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'YYYYMMDDHH',                'Compact hourly',       0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'YYYY-MM-DD HH:MM',          'ISO 24-hour',          0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'DD/MM/YYYY HH:MM',          'EU slash 24-hour',     0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'DD-MM-YYYY HH:MM',          'EU dash 24-hour',      0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'MM-DD-YYYY HH:MM',          'US dash 24-hour',      0);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (19);
