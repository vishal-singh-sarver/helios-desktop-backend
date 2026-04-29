-- Migration 011 — seed default weather data types and their units.
--
-- Per the design doc: standard weather parameters used by the simulation
-- models (radiation, energy balance, photosynthesis, etc.) along with
-- their conversion factors back to a canonical base unit per type.
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

INSERT OR IGNORE INTO helios_data_types (data_type, description) VALUES
    ('Direct Normal Radiation',      'Radiative flux normal to the direction of radiation propagation'),
    ('Diffuse Horizontal Radiation', 'Diffused solar radiation reaching the surface'),
    ('Air Temperature',              'Ambient air temperature at the location'),
    ('Air Pressure',                 'Atmospheric pressure value for the scenario'),
    ('Air Humidity',                 'Relative humidity level of the air'),
    ('Wind Speed',                   'Wind velocity affecting the environment'),
    ('Turbidity',                    'Clarity of the atmosphere, affecting sunlight scattering'),
    ('Beta Soil',                    'Soil moisture factor (effective water content vs field capacity / wilting point)'),
    ('Air CO2',                      'CO2 concentration of air outside primitive boundary-layer');

-- ── Direct Normal Radiation: base = W/m^2 ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'), 'W/m^2',  'W/m²',  1.0,    0.0, 1, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'), 'kW/m^2', 'kW/m²', 1000.0, 0.0, 0, NULL, NULL);

-- ── Diffuse Horizontal Radiation: base = W/m^2 ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'), 'W/m^2',  'W/m²',  1.0,    0.0, 1, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'), 'kW/m^2', 'kW/m²', 1000.0, 0.0, 0, NULL, NULL);

-- ── Air Temperature: base = Kelvin ──
-- C → K: K = C + 273.15
-- F → K: K = (F + 459.67) * 5/9   →   factor 5/9, offset 459.67 * 5/9 ≈ 255.3722
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Temperature'), 'K', 'Kelvin',     1.0,                 0.0,                 1, 223, 350),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Temperature'), 'C', 'Celsius',    1.0,                 273.15,              0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Temperature'), 'F', 'Fahrenheit', 0.5555555555555556,  255.3722222222222,   0, NULL, NULL);

-- ── Air Pressure: base = Pascal ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'Pa',   'Pascal',                  1.0,           0.0, 1, 87000, 150000),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'hPa',  'hectopascal',             100.0,         0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'kPa',  'kilopascal',              1000.0,        0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'atm',  'atmosphere',              101325.0,      0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'bar',  NULL,                      100000.0,      0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Pressure'), 'mmHg', 'millimetres of mercury',  133.322387415, 0.0, 0, NULL, NULL);

-- ── Air Humidity: base = fraction (0-1) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Humidity'), 'fraction', NULL,      1.0,  0.0, 1, 0, 1),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air Humidity'), '%',        'percent', 0.01, 0.0, 0, NULL, NULL);

-- ── Wind Speed: base = m/s ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Wind Speed'), 'm/s',   'metres per second',   1.0,                 0.0, 1, 0, 60),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Wind Speed'), 'km/h',  'kilometres per hour', 0.2777777777777778,  0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Wind Speed'), 'mph',   'miles per hour',      0.44704,             0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Wind Speed'), 'knots', NULL,                  0.5144444444444445,  0.0, 0, NULL, NULL),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Wind Speed'), 'ft/s',  'feet per second',     0.3048,              0.0, 0, NULL, NULL);

-- ── Turbidity: base = unitless (single unit) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Turbidity'), 'unitless', NULL, 1.0, 0.0, 1, 0, 10);

-- ── Beta Soil: base = unitless (single unit, coefficient 0-1) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Beta Soil'), 'unitless', NULL, 1.0, 0.0, 1, 0, 1);

-- ── Air CO2: base = umol/mol (== ppm) ──
INSERT OR IGNORE INTO data_units (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air CO2'), 'umol/mol', 'ppm', 1.0,   0.0, 1, 0, 3000),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Air CO2'), 'ppb',      NULL,  0.001, 0.0, 0, NULL, NULL);

INSERT OR IGNORE INTO schema_migrations(version) VALUES (11);
