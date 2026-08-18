-- Migration 031 — RADIATION: spectrum-label properties + the visibility row
-- migration 029 missed.
--
-- ── Part 1: the Radiation visibility rows migration 029 missed ───────────────
--
-- Migration 029 tagged every material type EXCEPT Radiation, so these stayed at
-- the 'editable' default and the catalog kept returning them. The bespoke
-- Radiation editor hides them with a hand-written list (materialBlueprint.ts
-- RADIATION_HIDDEN_PROPERTIES), but the read-only popup on a geometry has no
-- such list and renders whatever the catalog returns — so the same material
-- shows these fields on one screen and not the other.
--
-- TWO different tags, deliberately not one:
--   surface_temperature -> 'computed'   genuinely produced by Energy Balance;
--       the same call 029 made for Boundary Layer Conductance's copy. Should
--       come back if that model is ever disabled.
--   the broadband trio  -> 'superseded' NEW tag. Not weather, not computed —
--       replaced by the per-band PAR/NIR/LW values (migration 023). A future
--       "disable model -> enter input" pass will walk 'computed' rows and
--       resurrect them; tagging the trio 'computed' would wrongly bring back
--       outdated fields months from now.
--
-- The new tag needs no schema or code change: visibility has no CHECK
-- constraint (029 added it as plain TEXT DEFAULT 'editable'), and
-- _material_type_payload returns only visibility = 'editable', withholding
-- everything else automatically.
--
-- All four still validate, still store, and still reach the engine —
-- load_type_properties is unfiltered; only the catalog payload withholds them.
--
-- ── Part 2: reflectivity_spectrum / transmissivity_spectrum ──────────────────
--
-- "Apply spectral data" already stores an XML file (spectral_data, migration
-- 017), but one such file holds MANY spectra — each a
-- <globaldata_vec2 label="…"> block — so the file alone does not say which
-- curve this material is. These two properties carry that choice. Both are
-- optional: with neither set the engine keeps its default reflectivity /
-- transmissivity, exactly as today.
--
-- The NAMES are the contract, not decoration: RadiationModel reads per-primitive
-- STRING data called "reflectivity_spectrum" / "transmissivity_spectrum" and
-- resolves the value as a global-data label (RadiationModel.cpp ~2317). Naming
-- the properties identically means material_apply's generic string channel
-- (setPrimitiveDataString) delivers them to the engine with NO apply-layer
-- change — the same reason migration 023 names the band trio the way it does.
--
-- Additive seed only — no schema change and NO new table: the chosen labels are
-- ordinary EAV values in material_data. Idempotent (INSERT OR IGNORE on
-- property_type.property and material_property_type's (type, property) pair;
-- the UPDATE is deterministic). display_order 19..20 follows the bands (9..18)
-- and precedes the visualisation block (90..93). visibility defaults to
-- 'editable' (migration 029) so both reach the catalog endpoint — the form needs
-- them to render the pickers. Free-form strings, not enums: the valid values are
-- whatever labels the material's own uploaded file contains, so the value set
-- changes per upload and cannot be seeded.

-- ── Part 1 ───────────────────────────────────────────────────────────────────

-- Computed by the Energy Balance model — should return if that model is disabled.
UPDATE material_property_type SET visibility = 'computed'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Radiation')
  AND property_type_id IN (SELECT id FROM property_type WHERE property = 'surface_temperature');

-- Superseded by the per-band PAR/NIR/LW trio (migration 023): a distinct tag so
-- a later pass over 'computed' rows never resurrects them.
UPDATE material_property_type SET visibility = 'superseded'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Radiation')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('reflectivity', 'transmissivity', 'emissivity'));

-- ── Part 2 ───────────────────────────────────────────────────────────────────

-- Temporary list of new properties to register.
WITH p(property, description, dt) AS (VALUES
    ('reflectivity_spectrum',
     'Label of the reflectivity spectrum within the spectral data file',   'string'),
    ('transmissivity_spectrum',
     'Label of the transmissivity spectrum within the spectral data file', 'string')
)
-- Register these properties in the existing property_type table.
INSERT OR IGNORE INTO property_type (property, description, datatype_id, min, max)
SELECT p.property, p.description,
       (SELECT d.id FROM datatype d WHERE d.name = p.dt),
       NULL, NULL
FROM p;

-- Map both onto the Radiation material type, inside a selector-gated group so
-- the client knows they belong with the spectral file rather than the top-level
-- settings grid. Same mechanism 027 used for the Stomatal Conductance
-- sub-models: the client shows the group only when the selector matches, and
-- omits its properties from the payload otherwise — no client-side rule about
-- these property names. use_radiation_bands = false is the state where
-- "Apply spectral data" is ON. selector_value is TEXT ('false', lowercase)
-- because the catalog carries selector metadata as text.
WITH m(prop, ord) AS (VALUES
    ('reflectivity_spectrum',   19),
    ('transmissivity_spectrum', 20)
)
INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, min_override, max_override, display_order,
     group_name, selector_property, selector_value)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Radiation'),
       pt.id, NULL, NULL, m.ord,
       'Spectrum', 'use_radiation_bands', 'false'
FROM m JOIN property_type pt ON pt.property = m.prop;

-- The INSERT above is OR IGNORE, so it does nothing on a database that already
-- ran an earlier version of this migration — the rows exist, but without the
-- group columns. This UPDATE carries those databases forward; on a fresh one it
-- simply rewrites what the INSERT just set.
UPDATE material_property_type
SET group_name        = 'Spectrum',
    selector_property = 'use_radiation_bands',
    selector_value    = 'false'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Radiation')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('reflectivity_spectrum', 'transmissivity_spectrum'));

INSERT OR IGNORE INTO schema_migrations(version) VALUES (31);
