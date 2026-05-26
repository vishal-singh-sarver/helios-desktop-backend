-- Migration 015 — add Julian (day-of-year) datetime format units.
--
-- Two combined-datetime formats keyed on DOY (day-of-year, 1–366):
--   YYYY DOY HH:MM   → e.g. "2026 142 14:30"
--   DOY YYYY HH:MM   → e.g. "142 2026 14:30"
--
-- Migration 013 deliberately excluded these per the source doc's note
-- that they're uncommon. Adding now per product decision.
--
-- Scope: catalog-only — same as 013. Two new rows under the existing
-- `date_time` data type. is_base stays at 0 for both; the existing
-- default `MM/DD/YYYY HH:MM` is unchanged.
--
-- Parser support is a SEPARATE task: `_transform_csv` in
-- weather_service.py does not yet understand DOY columns. Until that
-- lands, CSV uploads using these formats will fail to parse — the
-- catalog rows exist so the frontend can list them, but actual upload
-- support needs `_find_julian_datetime_column` + `_parse_julian_datetime`
-- wired into the existing sniff/parse flow.

INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, is_base) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'YYYY DOY HH:MM', 'Julian (year first)', 0),
    ((SELECT id FROM helios_data_types WHERE data_type = 'date_time'),
        'DOY YYYY HH:MM', 'Julian (DOY first)',  0);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (15);
