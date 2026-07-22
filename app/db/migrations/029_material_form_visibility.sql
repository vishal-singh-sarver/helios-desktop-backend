-- Migration 029 — MATERIAL FORM VISIBILITY: catalog returns only the light-green
-- (editable) parameters/sub-parameters from the spec sheet.
--
-- Story: the material form shows ONLY the light-green rows. The sheet's white
-- rows are of TWO kinds, so a boolean won't do:
--   external  – Weather Data / Global params, set elsewhere (Weather panel /
--               project header) — never in the material form.
--   computed  – "Computed by other models, can be specified if those models are
--               disabled" — hidden while the upstream model runs, editable when
--               it is off. Excluded from the response for now, but tagged so the
--               future "disable model -> enter input" feature needs no re-seed.
-- Everything else defaults to 'editable' (light green), including params not yet
-- in the sheet (e.g. the radiation-band trio) and the Visualiser's own props.
--
-- The catalog endpoint (_material_type_payload) returns only visibility='editable'
-- (recursing into groups); validation/apply keep EVERY property
-- (load_type_properties is unfiltered), so hidden values still validate and still
-- reach the engine. Append-only; idempotent (deterministic UPDATEs).

ALTER TABLE material_property_type ADD COLUMN visibility TEXT NOT NULL DEFAULT 'editable';

-- ── Energy Balance ──
UPDATE material_property_type SET visibility = 'computed'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Energy Balance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('radiation_flux', 'boundary_layer_conductance', 'moisture_conductance'));
UPDATE material_property_type SET visibility = 'external'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Energy Balance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('wind_speed', 'air_temperature', 'surface_humidity', 'air_humidity', 'air_pressure', 'other_surface_flux'));

-- ── Solar Position (entirely global / weather) ──
UPDATE material_property_type SET visibility = 'external'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Solar Position');

-- ── Photosynthesis ──
UPDATE material_property_type SET visibility = 'computed'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('radiation_flux', 'moisture_conductance', 'boundary_layer_conductance'));
UPDATE material_property_type SET visibility = 'external'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('surface_temperature', 'air_co2'));

-- ── Boundary Layer Conductance ──
UPDATE material_property_type SET visibility = 'computed'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Boundary Layer Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('surface_temperature'));
UPDATE material_property_type SET visibility = 'external'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Boundary Layer Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('wind_speed'));

-- ── Stomatal Conductance ──
UPDATE material_property_type SET visibility = 'computed'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('radiation_flux', 'boundary_layer_conductance', 'net_photosynthesis'));
UPDATE material_property_type SET visibility = 'external'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN
      ('surface_temperature', 'beta_soil', 'air_temperature', 'air_humidity', 'air_pressure'));

INSERT OR IGNORE INTO schema_migrations(version) VALUES (29);
