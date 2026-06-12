-- Migration 017 — Materials & Geometry persistence (Milestone 2). see docs/api/milestone-2-materials-geometry.md (helios_gui repo)
--
-- Table groups (dependency order):
--   Catalog  : datatype, property_type
--   Geometry : object_types, object_property_type, object_group,
--              scenario_object
--   Materials: material_type, material_property_type, project_material,
--              material_data
--   Assign   : object_material, object_property_data
--              (object_property_data holds BOTH intrinsic geometry params,
--               project_material_id NULL, and frozen material values,
--               project_material_id NOT NULL - it is created after
--               object_material because of the composite FK)
--
-- No triggers (runner splits on semicolons). updated_at is ORM-maintained.
-- Value-range/datatype validity of `value` columns is enforced at the
-- service layer (catalog-driven validation), not in the schema.

CREATE TABLE IF NOT EXISTS datatype (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS property_type (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    property    TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT,
    datatype_id INTEGER NOT NULL REFERENCES datatype(id) ON DELETE RESTRICT,
    min         REAL,
    max         REAL,
    enum_values TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS object_types (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    object     TEXT NOT NULL UNIQUE COLLATE NOCASE,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS object_property_type (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type_id   INTEGER NOT NULL REFERENCES object_types(id)  ON DELETE CASCADE,
    property_type_id INTEGER NOT NULL REFERENCES property_type(id) ON DELETE CASCADE,
    min_override     REAL,
    max_override     REAL,
    display_order    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (object_type_id, property_type_id)
);

CREATE TABLE IF NOT EXISTS object_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    project_id  TEXT NOT NULL REFERENCES projects(id)  ON DELETE CASCADE,
    name        TEXT NOT NULL COLLATE NOCASE
                    CHECK (length(name) BETWEEN 1 AND 20),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_object_group_scenario
    ON object_group(scenario_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_object_group_project_name_ci
    ON object_group(project_id, name);

CREATE TABLE IF NOT EXISTS scenario_object (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_id    TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    project_id     TEXT NOT NULL REFERENCES projects(id)  ON DELETE CASCADE,
    name           TEXT NOT NULL COLLATE NOCASE
                       CHECK (length(name) BETWEEN 1 AND 20),
    object_type_id INTEGER NOT NULL REFERENCES object_types(id) ON DELETE RESTRICT,
    group_id       INTEGER REFERENCES object_group(id) ON DELETE SET NULL,
    helios_uuids   TEXT NOT NULL DEFAULT '[]',
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_scenario_object_scenario
    ON scenario_object(scenario_id);
CREATE INDEX IF NOT EXISTS idx_scenario_object_group
    ON scenario_object(group_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenario_object_project_name_ci
    ON scenario_object(project_id, name);

CREATE TABLE IF NOT EXISTS material_type (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    materialtype TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description  TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS material_property_type (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    material_type_id INTEGER NOT NULL REFERENCES material_type(id) ON DELETE CASCADE,
    property_type_id INTEGER NOT NULL REFERENCES property_type(id) ON DELETE CASCADE,
    min_override     REAL,
    max_override     REAL,
    display_order    INTEGER NOT NULL DEFAULT 0,
    UNIQUE (material_type_id, property_type_id)
);

CREATE TABLE IF NOT EXISTS project_material (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    scenario_id      TEXT REFERENCES scenarios(id) ON DELETE SET NULL,
    material_type_id INTEGER NOT NULL REFERENCES material_type(id) ON DELETE RESTRICT,
    name             TEXT NOT NULL COLLATE NOCASE
                         CHECK (length(name) BETWEEN 1 AND 20),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (id, material_type_id)
);

CREATE INDEX IF NOT EXISTS idx_project_material_project
    ON project_material(project_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_material_project_name_ci
    ON project_material(project_id, name);

CREATE TABLE IF NOT EXISTS material_data (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    project_material_id INTEGER NOT NULL REFERENCES project_material(id) ON DELETE CASCADE,
    property_type_id    INTEGER NOT NULL REFERENCES property_type(id)    ON DELETE RESTRICT,
    value               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (project_material_id, property_type_id)
);

CREATE INDEX IF NOT EXISTS idx_material_data_material
    ON material_data(project_material_id);

CREATE TABLE IF NOT EXISTS object_material (
    scenario_object_id  INTEGER NOT NULL REFERENCES scenario_object(id) ON DELETE CASCADE,
    project_material_id INTEGER NOT NULL,
    material_type_id    INTEGER NOT NULL REFERENCES material_type(id) ON DELETE RESTRICT,
    sync                INTEGER NOT NULL DEFAULT 1,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scenario_object_id, project_material_id),
    UNIQUE (scenario_object_id, material_type_id),
    FOREIGN KEY (project_material_id, material_type_id)
        REFERENCES project_material(id, material_type_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_object_material_material
    ON object_material(project_material_id);

CREATE TABLE IF NOT EXISTS object_property_data (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    scenario_object_id  INTEGER NOT NULL REFERENCES scenario_object(id) ON DELETE CASCADE,
    project_material_id INTEGER,
    property_type_id    INTEGER NOT NULL REFERENCES property_type(id) ON DELETE RESTRICT,
    value               TEXT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (scenario_object_id, project_material_id)
        REFERENCES object_material(scenario_object_id, project_material_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_object_property_data_object
    ON object_property_data(scenario_object_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_opd_intrinsic
    ON object_property_data(scenario_object_id, property_type_id)
    WHERE project_material_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_opd_frozen
    ON object_property_data(scenario_object_id, project_material_id, property_type_id)
    WHERE project_material_id IS NOT NULL;

-- ── Seeds: datatypes ──

INSERT OR IGNORE INTO datatype (name) VALUES
    ('float'), ('integer'), ('boolean'), ('string'),
    ('date'), ('time'), ('file'), ('enum');

-- ── Seeds: object types (Crop has no property links yet — TBD) ──

INSERT OR IGNORE INTO object_types (object) VALUES ('Ground'), ('Crop');

-- ── Seeds: material types ──

INSERT OR IGNORE INTO material_type (materialtype, description) VALUES
    ('Radiation',                  'Optical and thermal-radiative surface properties'),
    ('Energy Balance',             'Surface energy balance model inputs'),
    ('Solar Position',             'Sun position and atmospheric inputs'),
    ('Photosynthesis',             'Farquhar photosynthesis model parameters'),
    ('Boundary Layer Conductance', 'Boundary layer conductance model selection and inputs'),
    ('Stomatal Conductance',       'Stomatal conductance sub-models and coefficients');

-- ── Seeds: property_type (non-enum) ──

WITH p(property, description, dt, mn, mx) AS (VALUES
    ('length',                     'Ground size L in meters',                          'float',   0,        NULL),
    ('breadth',                    'Ground size B in meters',                          'float',   0,        NULL),
    ('resolution_x',               'Ground resolution along X',                        'integer', 1,        25000),
    ('resolution_y',               'Ground resolution along Y',                        'integer', 1,        25000),
    ('position_x',                 'Position X',                                       'float',   NULL,     NULL),
    ('position_y',                 'Position Y',                                       'float',   NULL,     NULL),
    ('position_z',                 'Position Z',                                       'float',   NULL,     NULL),
    ('rotation_z',                 'Rotation about the z-axis in degrees',             'float',   0,        360),
    ('texture_x',               'Texture repeat count along X',                           'integer', 1,        NULL),
    ('texture_y',            'Texture repeat count along Y',                        'integer', 1,        NULL),
    ('color_r',                    'Visualisation color red channel',                  'integer', 0,        255),
    ('color_g',                    'Visualisation color green channel',                'integer', 0,        255),
    ('color_b',                    'Visualisation color blue channel',                 'integer', 0,        255),
    ('texture_file',               'Texture image file',                               'file',    NULL,     NULL),
    ('surface_temperature',        'Surface temperature in Kelvin',                    'float',   223,      5000),
    ('reflectivity',               'Fraction of incident radiation reflected',         'float',   0,        1),
    ('transmissivity',             'Fraction of incident radiation transmitted',       'float',   0,        1),
    ('emissivity',                 'Thermal emissivity',                               'float',   0,        1),
    ('specular_exponent',          'Specular reflection exponent',                     'float',   1,        1000),
    ('specular_scale',             'Specular reflection scale',                        'float',   0,        100),
    ('two_sided_heat_transfer',    'Heat transfer occurs on both primitive faces',     'boolean', NULL,     NULL),
    ('spectral_data',              'Spectral data file',                               'file',    NULL,     NULL),
    ('radiation_flux',             'Absorbed radiation flux in W per m2',              'float',   0,        10000000),
    ('boundary_layer_conductance', 'Boundary layer conductance in mol per m2 per s',   'float',   0,        100),
    ('moisture_conductance',       'Moisture conductance in mol per m2 per s',         'float',   0,        100),
    ('stomatal_sidedness',         'Stomatal sidedness',                               'float',   0,        1),
    ('object_length',              'Characteristic object length in meters',           'float',   0.000001, 1000000),
    ('heat_capacity',              'Heat capacity in J per kg K',                      'float',   0,        1000000),
    ('wind_speed',                 'Wind speed in m per s',                            'float',   0,        60),
    ('air_temperature',            'Air temperature in Kelvin',                        'float',   223,      5000),
    ('surface_humidity',           'Surface humidity',                                 'float',   0,        1),
    ('air_humidity',               'Air humidity',                                     'float',   0,        1),
    ('air_pressure',               'Air pressure in Pascals',                          'float',   87000,    150000),
    ('other_surface_flux',         'Other surface flux in W per m2',                   'float',   -1000000, 1000000),
    ('utc',                        'UTC offset in hours',                              'float',   -12,      14),
    ('date',                       'Date',                                             'date',    NULL,     NULL),
    ('time',                       'Time of day',                                      'time',    NULL,     NULL),
    ('latitude',                   'Latitude in degrees',                              'float',   -90,      90),
    ('longitude',                  'Longitude in degrees',                             'float',   -180,     180),
    ('atmospheric_pressure',       'Atmospheric pressure',                             'float',   NULL,     NULL),
    ('atmospheric_temperature',    'Atmospheric temperature',                          'float',   NULL,     NULL),
    ('atmospheric_humidity',       'Atmospheric humidity',                             'float',   NULL,     NULL),
    ('atmospheric_turbidity',      'Atmospheric turbidity',                            'float',   NULL,     NULL),
    ('vcmax25',                    'Farquhar Vcmax at 25 C',                           'float',   0,        1000),
    ('jmax25',                     'Farquhar Jmax at 25 C',                            'float',   0,        1000),
    ('tpu25',                      'Farquhar TPU at 25 C',                             'float',   0,        100),
    ('rd25',                       'Farquhar dark respiration at 25 C',                'float',   0,        100),
    ('alpha',                      'Farquhar alpha',                                   'float',   0,        10),
    ('theta',                      'Farquhar theta',                                   'float',   0,        10),
    ('dha_vcmax',                  'Activation energy dHa for Vcmax',                  'float',   0,        500),
    ('topt_vcmax',                 'Optimum temperature for Vcmax in Kelvin',          'float',   273,      373),
    ('dha_jmax',                   'Activation energy dHa for Jmax',                   'float',   0,        500),
    ('topt_jmax',                  'Optimum temperature for Jmax in Kelvin',           'float',   273,      373),
    ('dhd_jmax',                   'Deactivation energy dHd for Jmax',                 'float',   0,        500),
    ('dha_tpu',                    'Activation energy dHa for TPU',                    'float',   0,        500),
    ('topt_tpu',                   'Optimum temperature for TPU in Kelvin',            'float',   272,      373),
    ('dhd_tpu',                    'Deactivation energy dHd for TPU',                  'float',   0,        500),
    ('air_co2',                    'Air CO2 concentration in micromol per mol',        'float',   0,        3000),
    ('net_photosynthesis',         'Net photosynthesis in micromol per m2 per s',      'float',   -100,     500),
    ('gamma_co2',                  'CO2 compensation point in micromol per mol',       'float',   0,        1000),
    ('beta_soil',                  'Soil moisture factor beta',                        'float',   0,        1),
    ('bwb_gs0',                    'Ball-Woodrow-Berry gs0',                           'float',   0,        1),
    ('bwb_a1',                     'Ball-Woodrow-Berry a1',                            'float',   0,        50),
    ('bbl_gs0',                    'Ball-Berry-Leuning gs0',                           'float',   0,        1),
    ('bbl_a1',                     'Ball-Berry-Leuning a1',                            'float',   0,        50),
    ('bbl_d0',                     'Ball-Berry-Leuning D0',                            'float',   0,        5000000),
    ('medlyn_gs0',                 'Medlyn optimality gs0',                            'float',   0,        1),
    ('medlyn_g1',                  'Medlyn optimality g1',                             'float',   0,        50),
    ('bmf_em',                     'Buckley-Mott-Farquhar Em',                         'float',   0,        50000),
    ('bmf_i0',                     'Buckley-Mott-Farquhar i0',                         'float',   0,        10000),
    ('bmf_k',                      'Buckley-Mott-Farquhar k',                          'float',   0,        10000000),
    ('bmf_b',                      'Buckley-Mott-Farquhar b',                          'float',   0,        50000)
)
INSERT OR IGNORE INTO property_type (property, description, datatype_id, min, max)
SELECT p.property, p.description,
       (SELECT d.id FROM datatype d WHERE d.name = p.dt),
       p.mn, p.mx
FROM p;

-- ── Seeds: property_type (enum) ──

INSERT OR IGNORE INTO property_type (property, description, datatype_id, enum_values) VALUES
    ('boundary_layer_model', 'Boundary layer conductance model',
        (SELECT id FROM datatype WHERE name = 'enum'),
        '["Pohlhausen", "InclinedPlate", "Sphere", "Ground"]'),
    ('stomatal_model', 'Stomatal conductance sub-model',
        (SELECT id FROM datatype WHERE name = 'enum'),
        '["BWB", "BBL", "Medlyn", "BMF"]');

-- ── Seeds: Ground object properties ──

WITH g(prop, ord) AS (VALUES
    ('length', 1), ('breadth', 2),
    ('resolution_x', 3), ('resolution_y', 4),
    ('position_x', 5), ('position_y', 6), ('position_z', 7),
    ('rotation_z', 8), ('texture_x', 9), ('texture_y', 10)
)
INSERT OR IGNORE INTO object_property_type (object_type_id, property_type_id, display_order)
SELECT (SELECT id FROM object_types WHERE object = 'Ground'), pt.id, g.ord
FROM g JOIN property_type pt ON pt.property = g.prop;

-- ── Seeds: visualisation properties on ALL material types ──

INSERT OR IGNORE INTO material_property_type (material_type_id, property_type_id, display_order)
SELECT mt.id, pt.id,
       CASE pt.property
           WHEN 'color_r' THEN 90
           WHEN 'color_g' THEN 91
           WHEN 'color_b' THEN 92
           ELSE 93
       END
FROM material_type mt
CROSS JOIN property_type pt
WHERE pt.property IN ('color_r', 'color_g', 'color_b', 'texture_file');

-- ── Seeds: Radiation model properties ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('surface_temperature',     NULL, NULL, 1),
    ('reflectivity',            NULL, NULL, 2),
    ('transmissivity',          NULL, NULL, 3),
    ('emissivity',              NULL, NULL, 4),
    ('specular_exponent',       NULL, NULL, 5),
    ('specular_scale',          NULL, NULL, 6),
    ('two_sided_heat_transfer', NULL, NULL, 7),
    ('spectral_data',           NULL, NULL, 8)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Radiation'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

-- ── Seeds: Energy Balance model properties ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('radiation_flux',             NULL, NULL, 1),
    ('boundary_layer_conductance', NULL, NULL, 2),
    ('moisture_conductance',       NULL, NULL, 3),
    ('two_sided_heat_transfer',    NULL, NULL, 4),
    ('stomatal_sidedness',         NULL, NULL, 5),
    ('object_length',              NULL, NULL, 6),
    ('heat_capacity',              NULL, NULL, 7),
    ('wind_speed',                 NULL, NULL, 8),
    ('air_temperature',            NULL, NULL, 9),
    ('surface_humidity',           NULL, NULL, 10),
    ('air_humidity',               NULL, NULL, 11),
    ('air_pressure',               NULL, NULL, 12),
    ('other_surface_flux',         NULL, NULL, 13)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Energy Balance'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

-- ── Seeds: Solar Position model properties ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('utc',                     NULL, NULL, 1),
    ('date',                    NULL, NULL, 2),
    ('time',                    NULL, NULL, 3),
    ('latitude',                NULL, NULL, 4),
    ('longitude',               NULL, NULL, 5),
    ('atmospheric_pressure',    NULL, NULL, 6),
    ('atmospheric_temperature', NULL, NULL, 7),
    ('atmospheric_humidity',    NULL, NULL, 8),
    ('atmospheric_turbidity',   NULL, NULL, 9)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Solar Position'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

-- ── Seeds: Photosynthesis model properties (range overrides on shared props) ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('radiation_flux',             0,    1500, 1),
    ('surface_temperature',        223,  400,  2),
    ('moisture_conductance',       NULL, NULL, 3),
    ('boundary_layer_conductance', NULL, NULL, 4),
    ('two_sided_heat_transfer',    NULL, NULL, 5),
    ('stomatal_sidedness',         NULL, NULL, 6),
    ('vcmax25',                    NULL, NULL, 7),
    ('jmax25',                     NULL, NULL, 8),
    ('tpu25',                      NULL, NULL, 9),
    ('rd25',                       NULL, NULL, 10),
    ('alpha',                      NULL, NULL, 11),
    ('theta',                      NULL, NULL, 12),
    ('dha_vcmax',                  NULL, NULL, 13),
    ('topt_vcmax',                 NULL, NULL, 14),
    ('dha_jmax',                   NULL, NULL, 15),
    ('topt_jmax',                  NULL, NULL, 16),
    ('dhd_jmax',                   NULL, NULL, 17),
    ('dha_tpu',                    NULL, NULL, 18),
    ('topt_tpu',                   NULL, NULL, 19),
    ('dhd_tpu',                    NULL, NULL, 20),
    ('air_co2',                    NULL, NULL, 21)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

-- ── Seeds: Boundary Layer Conductance model properties ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('surface_temperature',     223,  400,  1),
    ('two_sided_heat_transfer', NULL, NULL, 2),
    ('boundary_layer_model',    NULL, NULL, 3),
    ('wind_speed',              NULL, NULL, 4)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Boundary Layer Conductance'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

-- ── Seeds: Stomatal Conductance model properties ──

WITH m(prop, mn, mx, ord) AS (VALUES
    ('radiation_flux',             0,    1500, 1),
    ('surface_temperature',        223,  400,  2),
    ('boundary_layer_conductance', NULL, NULL, 3),
    ('net_photosynthesis',         NULL, NULL, 4),
    ('gamma_co2',                  NULL, NULL, 5),
    ('beta_soil',                  NULL, NULL, 6),
    ('air_temperature',            223,  400,  7),
    ('air_humidity',               NULL, NULL, 8),
    ('air_pressure',               NULL, NULL, 9),
    ('stomatal_model',             NULL, NULL, 10),
    ('bwb_gs0',                    NULL, NULL, 11),
    ('bwb_a1',                     NULL, NULL, 12),
    ('bbl_gs0',                    NULL, NULL, 13),
    ('bbl_a1',                     NULL, NULL, 14),
    ('bbl_d0',                     NULL, NULL, 15),
    ('medlyn_gs0',                 NULL, NULL, 16),
    ('medlyn_g1',                  NULL, NULL, 17),
    ('bmf_em',                     NULL, NULL, 18),
    ('bmf_i0',                     NULL, NULL, 19),
    ('bmf_k',                      NULL, NULL, 20),
    ('bmf_b',                      NULL, NULL, 21)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance'),
       pt.id, m.mn, m.mx, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (17);
