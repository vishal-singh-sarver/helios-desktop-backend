-- Migration 014 — add helios_data_types columns + restore radiation
-- energy-flux units and air_CO2 conversion details.
--
-- Background: an earlier set of migrations applied these changes to live
-- databases but their files were lost. When the migration history was
-- reset to a clean contiguous sequence, their surviving effects were
-- folded into this single migration so a fresh build reproduces the full
-- catalog.
--
-- helios_data_types gains two columns:
--   check          — boolean-style flag, reserved for checkbox data types.
--   parameter_type — how a type's units convert to its base unit:
--                      'linear_factor' — scale only (value * factor)
--                      'affine'        — scale + offset (value * f + o)
--                      'fixed'         — single unit, no conversion

-- ── (1) New helios_data_types columns ───────────────────────────────────
ALTER TABLE helios_data_types ADD COLUMN "check" INTEGER NOT NULL DEFAULT 1;
ALTER TABLE helios_data_types ADD COLUMN parameter_type TEXT NOT NULL DEFAULT 'linear_factor';

-- ── (2) parameter_type for the non-linear types ─────────────────────────
-- Everything else keeps the 'linear_factor' default.
UPDATE helios_data_types SET parameter_type = 'affine' WHERE data_type = 'air_temperature';
UPDATE helios_data_types SET parameter_type = 'fixed'  WHERE data_type = 'beta_soil';

-- ── (3) Radiation energy / photon-flux units ────────────────────────────
-- Migration 011 seeded only W/m^2 + kW/m^2 for the two radiation types
-- (it explicitly left these three out). Restored here for both types.
INSERT OR IGNORE INTO data_units
    (data_type_id, unit, alias, to_base_factor, to_base_offset, is_base, min, max) VALUES
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'),
        'Wh/m^2',      'Wh/m² (per hour)',   1.0,                0.0, 0, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'),
        'kWh/m^2/day', 'kWh/m²/day',         41.66666666666667,  0.0, 0, 0, 36),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Direct Normal Radiation'),
        'umol/m^2/s',  'μmol·m⁻²·s⁻¹ (PAR)', 0.2188183807439825, 0.0, 0, 0, 6855),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'),
        'Wh/m^2',      'Wh/m² (per hour)',   1.0,                0.0, 0, 0, 1500),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'),
        'kWh/m^2/day', 'kWh/m²/day',         41.66666666666667,  0.0, 0, 0, 36),
    ((SELECT id FROM helios_data_types WHERE data_type = 'Diffuse Horizontal Radiation'),
        'umol/m^2/s',  'μmol·m⁻²·s⁻¹ (PAR)', 0.2188183807439825, 0.0, 0, 0, 6855);

-- ── (4) air_CO2: aliases + the kg/m³→ppm conversion factor ──────────────
UPDATE data_units SET alias = 'ppm'
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_CO2')
      AND unit = 'ppm';
UPDATE data_units SET alias = 'kg/m³ (25°C, 1atm)', to_base_factor = 555864.3690939411
    WHERE data_type_id = (SELECT id FROM helios_data_types WHERE data_type = 'air_CO2')
      AND unit = 'kg/m³';

INSERT OR IGNORE INTO schema_migrations(version) VALUES (14);
