-- Migration 016 — seed default weather data types and their units.
--
-- Per the design doc: standard weather parameters used by the simulation
-- models (radiation, energy balance, photosynthesis, etc.) along with
-- their conversion factors back to a canonical base unit per type.
--
-- Naming: data_type values follow the doc's "Key used" column.
--   - air_temperature, air_pressure, air_humidity, wind_speed, turbidity,
--     beta_soil, air_CO2 — snake_case keys PyHelios expects when wiring
--     weather data into the simulation models.
--   - Direct Normal Radiation and Diffuse Horizontal Radiation — left
--     as Title Case because the doc's "Key used" column is empty for
--     these two; no canonical key is defined yet.
-- Description carries the human-readable form.
--
-- Each parameter has exactly one base unit (is_base=1) and zero or more
-- secondary units linearly convertible via:
--     value_in_base = value * to_base_factor + to_base_offset
--
-- Re-runnable: INSERT OR IGNORE skips entries already present (unique
-- constraints on data_type and (data_type_id, unit)).
--
-- Out of scope here:
--   - UTC, latitude, longitude, Date, Time — project/scenario metadata,
--     not measured weather data.
--   - Wh/m^2 / kWh/m^2/day / micromol/m^2/s under radiation — these are
--     energy or photon flux, not linearly convertible to W/m^2.
--   - kg/m^3 under CO2 — needs molar mass / ideal gas law to convert to
--     ppm, not a simple linear transform.
--   These are intentionally omitted; add later if a non-linear conversion
--   path is wired up.

-- ── Standardize existing units (Renames instead of Deletes to avoid FK errors) ──
UPDATE data_units SET unit = '0-1'   WHERE data_type_id = 5 AND unit = 'fraction';
UPDATE data_units SET unit = '0-100' WHERE data_type_id = 5 AND unit = '%';
UPDATE data_units SET unit = '0-1'   WHERE data_type_id = 7 AND unit = 'unitless';
UPDATE data_units SET unit = '0-1'   WHERE data_type_id = 8 AND unit = 'unitless';
UPDATE data_units SET unit = 'ppm'   WHERE data_type_id = 9 AND unit = 'umol/mol';
UPDATE data_units SET unit = 'kg/m³' WHERE data_type_id = 9 AND unit = 'kg/m^3';

INSERT OR IGNORE INTO helios_data_types (data_type, description) VALUES
    ('Direct Normal Radiation',      'Radiative flux normal to the direction of radiation propagation'),
    ('Diffuse Horizontal Radiation', 'Diffused solar radiation reaching the surface'),
    ('air_temperature',              'Ambient air temperature at the location'),
    ('air_pressure',                 'Atmospheric pressure value for the scenario'),
    ('air_humidity',                 'Relative humidity level of the air'),
    ('wind_speed',                   'Wind velocity affecting the environment'),
    ('turbidity',                    'Clarity of the atmosphere, affecting sunlight scattering'),
    ('beta_soil',                    'Soil moisture factor (effective water content vs field capacity / wilting point)'),
    ('air_CO2',                      'CO2 concentration of air outside primitive boundary-layer'),
    ('check',                        'Boolean / checkbox-style measurement (no units)');

-- ── Direct Normal Radiation: base = W/m^2 (no snake_case key in doc) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'), 'W/m^2',  'W/m²',  1.0,    0.0, 1, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'), 'kW/m^2', 'kW/m²', 1000.0, 0.0, 0, NULL, NULL);

-- ── Diffuse Horizontal Radiation: base = W/m^2 (no snake_case key in doc) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'), 'W/m^2',  'W/m²',  1.0,    0.0, 1, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'), 'kW/m^2', 'kW/m²', 1000.0, 0.0, 0, NULL, NULL);

-- ── air_temperature: base = Kelvin ──
-- C → K: K = C + 273.15
-- F → K: K = (F + 459.67) * 5/9   →   factor 5/9, offset 459.67 * 5/9 ≈ 255.3722
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_temperature'), 'K', 'Kelvin',     1.0,                 0.0,                 1, 223, 350),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_temperature'), 'C', 'Celsius',    1.0,                 273.15,              0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_temperature'), 'F', 'Fahrenheit', 0.5555555555555556,  255.3722222222222,   0, NULL, NULL);

-- ── air_pressure: base = Pascal ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'Pa',   'Pascal',                  1.0,           0.0, 1, 87000, 150000),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'hPa',  'hectopascal',             100.0,         0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'kPa',  'kilopascal',              1000.0,        0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'atm',  'atmosphere',              101325.0,      0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'bar',  NULL,                      100000.0,      0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_pressure'), 'mmHg', 'millimetres of mercury',  133.322387415, 0.0, 0, NULL, NULL);

-- ── air_humidity: base = fraction (0-1) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_humidity'), '0-1',   NULL,      1.0,  0.0, 1, 0, 1),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_humidity'), '0-100', 'percent', 0.01, 0.0, 0, NULL, NULL);

-- ── wind_speed: base = m/s ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'wind_speed'), 'm/s',   'metres per second',   1.0,                 0.0, 1, 0, 60),
    ((SELECT id FROM helios_data_types WHERE data_type = 'wind_speed'), 'km/h',  'kilometres per hour', 0.2777777777777778,  0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'wind_speed'), 'mph',   'miles per hour',      0.44704,             0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'wind_speed'), 'knots', NULL,                  0.5144444444444445,  0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'wind_speed'), 'ft/s',  'feet per second',     0.3048,              0.0, 0, NULL, NULL);

-- ── turbidity: base = unitless (single unit) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'turbidity'), '0-1', NULL, 1.0, 0.0, 1, 0, 10),
    ((SELECT id FROM helios_data_types WHERE data_type = 'turbidity'), '>1',  NULL, 1.0, 0.0, 0, NULL, NULL);

-- ── beta_soil: base = unitless (single unit, coefficient 0-1) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'beta_soil'), '0-1', NULL, 1.0, 0.0, 1, 0, 1);

-- ── air_CO2: base = umol/mol (== ppm) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_CO2'), 'ppm',   NULL, 1.0,   0.0, 1, 0, 3000),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_CO2'), 'ppb',   NULL, 0.001, 0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'air_CO2'), 'kg/m³', NULL, 1.0,   0.0, 0, NULL, NULL);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (17);
