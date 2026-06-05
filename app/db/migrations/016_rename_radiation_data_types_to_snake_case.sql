-- Migration 016 — rename radiation data types to snake_case + correct the
-- physical description.
--
-- Background: 'Direct Normal Radiation' was misleading because weather data
-- files (and the radiometers that produce them) almost always provide
-- radiation flux on a HORIZONTAL surface — not flux normal to the sun
-- direction. The old name and the old description ("Radiative flux normal
-- to the direction of radiation propagation") told users the wrong thing
-- about what value belongs in that column.
--
-- This migration renames both radiation entries to reflect what's actually
-- stored, and aligns them with the snake_case convention used by every
-- other catalog entry (air_temperature, wind_speed, date_time, etc.).
--
-- If/when these values get consumed directly by a radiation model (which
-- expects flux normal to the sun direction), a downstream warning should
-- surface that the stored value is horizontal and needs cos(zenith)
-- correction. No such code path exists in the backend today.
--
-- Safety: both UPDATEs are idempotent — a DB that already has the new
-- names matches 0 rows and the statement is a no-op. helios_data_types.id
-- is unchanged, so foreign keys in data_units and weather_data_headers
-- continue to point correctly.

UPDATE helios_data_types
    SET data_type   = 'direct_horizontal_radiation_flux',
        description = 'Direct solar radiative flux on a horizontal surface (as measured by a radiometer)'
    WHERE data_type = 'Direct Normal Radiation';

UPDATE helios_data_types
    SET data_type   = 'diffuse_horizontal_radiation_flux',
        description = 'Diffuse solar radiative flux on a horizontal surface'
    WHERE data_type = 'Diffuse Horizontal Radiation';

INSERT OR IGNORE INTO schema_migrations(version) VALUES (16);
