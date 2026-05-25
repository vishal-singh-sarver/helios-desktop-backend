-- Migration 012 — set min/max on all secondary (non-base) weather data units.
--
-- Migration 011 seeded data types and units but only filled min/max on the
-- base unit of each parameter. This migration populates the per-unit
-- ranges for every other unit, using the Weather Parameter Unit
-- Conversion Reference doc as the source of truth.
--
-- Also corrects two pre-existing values to match the doc:
--   - turbidity '0-1' max: 10 → 1   (normalized range is 0–1, not 0–10)
--   - air_CO2 'kg/m³' max: 0.005397 → 0.005894  (switch from 25 °C/1 atm
--     to STP — 0 °C/1 atm — per doc)
--
-- For turbidity '>1' (the "Extended" row): the doc specifies no upper
-- bound, so only min is set; max stays NULL.

-- ── Corrections to existing values ──────────────────────────────────────
UPDATE data_units SET max = 1
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'turbidity')
      AND unit = '0-1';

UPDATE data_units SET max = 0.005894
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_CO2')
      AND unit = 'kg/m³';

-- ── Direct Normal Radiation: kW/m² ──────────────────────────────────────
UPDATE data_units SET min = 0, max = 1.5
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation')
      AND unit = 'kW/m^2';

-- ── Diffuse Horizontal Radiation: kW/m² ─────────────────────────────────
UPDATE data_units SET min = 0, max = 1.5
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation')
      AND unit = 'kW/m^2';

-- ── air_temperature: C, F ───────────────────────────────────────────────
UPDATE data_units SET min = -50.15, max = 76.85
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_temperature')
      AND unit = 'C';
UPDATE data_units SET min = -58.27, max = 170.33
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_temperature')
      AND unit = 'F';

-- ── air_pressure: hPa, kPa, atm, bar, mmHg ──────────────────────────────
UPDATE data_units SET min = 870, max = 1500
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_pressure')
      AND unit = 'hPa';
UPDATE data_units SET min = 87, max = 150
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_pressure')
      AND unit = 'kPa';
UPDATE data_units SET min = 0.8586, max = 1.4805
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_pressure')
      AND unit = 'atm';
UPDATE data_units SET min = 0.87, max = 1.5
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_pressure')
      AND unit = 'bar';
UPDATE data_units SET min = 652.55, max = 1125.09
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_pressure')
      AND unit = 'mmHg';

-- ── air_humidity: 0-100 ─────────────────────────────────────────────────
UPDATE data_units SET min = 0, max = 100
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_humidity')
      AND unit = '0-100';

-- ── wind_speed: km/h, mph, knots, ft/s ──────────────────────────────────
UPDATE data_units SET min = 0, max = 216
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'wind_speed')
      AND unit = 'km/h';
UPDATE data_units SET min = 0, max = 134.22
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'wind_speed')
      AND unit = 'mph';
UPDATE data_units SET min = 0, max = 116.63
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'wind_speed')
      AND unit = 'knots';
UPDATE data_units SET min = 0, max = 196.85
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'wind_speed')
      AND unit = 'ft/s';

-- ── turbidity: >1 (open-ended — min only) ───────────────────────────────
UPDATE data_units SET min = 1
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'turbidity')
      AND unit = '>1';

-- ── air_CO2: ppb ────────────────────────────────────────────────────────
UPDATE data_units SET min = 0, max = 3000000
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_CO2')
      AND unit = 'ppb';

INSERT OR IGNORE INTO schema_migrations(version) VALUES (12);
