-- Migration 023 — RADIATION BANDS: per-band optical properties (PAR/NIR/LW).
--
-- Story: the Radiation material type gains per-band reflectivity/transmissivity/
-- emissivity for three wavebands (PAR, NIR, LW) as an alternative to the single
-- broadband trio. A boolean flag use_radiation_bands persists whether the
-- material is in banded mode; the frontend disables the broadband three when it
-- is on. Materials have no required-property mechanism, so no backend gating is
-- needed. Band property NAMES match Helios' per-primitive radiative-data
-- convention (reflectivity_<band> etc.) so material_apply writes them to the
-- engine unchanged.
--
-- Additive seed only — no schema/table change. Idempotent: INSERT OR IGNORE on
-- property_type.property (UNIQUE) and material_property_type's (type, property)
-- UNIQUE pair; safe to re-run. Band fractions inherit the property_type 0..1
-- range (no per-material override). display_order 9..18 sits after the existing
-- Radiation props (1..8) and before the visualisation block (90..93).

-- Seed 9 per-band radiation floats [0,1] plus the use_radiation_bands boolean.
WITH p(property, description, dt, mn, mx) AS (VALUES
    ('reflectivity_PAR',    'PAR-band reflectivity',              'float',   0,    1),
    ('transmissivity_PAR',  'PAR-band transmissivity',            'float',   0,    1),
    ('emissivity_PAR',      'PAR-band emissivity',                'float',   0,    1),
    ('reflectivity_NIR',    'NIR-band reflectivity',              'float',   0,    1),
    ('transmissivity_NIR',  'NIR-band transmissivity',            'float',   0,    1),
    ('emissivity_NIR',      'NIR-band emissivity',                'float',   0,    1),
    ('reflectivity_LW',     'LW-band reflectivity',               'float',   0,    1),
    ('transmissivity_LW',   'LW-band transmissivity',             'float',   0,    1),
    ('emissivity_LW',       'LW-band emissivity',                 'float',   0,    1),
    ('use_radiation_bands', 'Use per-band radiation properties',  'boolean', NULL, NULL)
)
INSERT OR IGNORE INTO property_type (property, description, datatype_id, min, max)
SELECT p.property, p.description,
       (SELECT d.id FROM datatype d WHERE d.name = p.dt),
       p.mn, p.mx
FROM p;

-- Map all 10 onto the Radiation material type (ranges inherited).
WITH m(prop, ord) AS (VALUES
    ('use_radiation_bands',  9),
    ('reflectivity_PAR',    10),
    ('transmissivity_PAR',  11),
    ('emissivity_PAR',      12),
    ('reflectivity_NIR',    13),
    ('transmissivity_NIR',  14),
    ('emissivity_NIR',      15),
    ('reflectivity_LW',     16),
    ('transmissivity_LW',   17),
    ('emissivity_LW',       18)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Radiation'),
       pt.id, NULL, NULL, m.ord
FROM m JOIN property_type pt ON pt.property = m.prop;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (23);
