-- Migration 027 — MATERIAL PARAMETER GROUPS: parent/child grouping + display labels.
--
-- Story (M2 material form): some parameters render inside a NAMED sub-group,
-- not flat. Two shapes:
--   * Photosynthesis   -> a "Farquhar model" collapsible group (14 params).
--   * Stomatal Conductance -> four MUTUALLY-EXCLUSIVE sub-models chosen by the
--     `stomatal_model` enum; only the selected sub-model's params are shown/saved.
--
-- The property NAMES stay unique per sub-model (bwb_gs0 vs bbl_gs0 vs
-- medlyn_gs0), so a save is never ambiguous. `label` is DISPLAY-ONLY and may
-- repeat across sub-models ("gs, o") — it never becomes the storage key.
--
-- Adds four nullable columns to the link table (per-link metadata, same shape as
-- min_override / display_order) and backfills Photosynthesis + Stomatal
-- Conductance. Append-only; idempotent: duplicate-column ALTERs are skipped by
-- the runner (_ALREADY_APPLIED_ERRORS), and the UPDATEs are deterministic.

ALTER TABLE material_property_type ADD COLUMN group_name        TEXT;
ALTER TABLE material_property_type ADD COLUMN selector_property TEXT;
ALTER TABLE material_property_type ADD COLUMN selector_value    TEXT;
ALTER TABLE material_property_type ADD COLUMN label             TEXT;

-- ── Photosynthesis: "Farquhar model" collapsible group (no selector) ──
UPDATE material_property_type
SET group_name = 'Farquhar model'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN (
      'vcmax25', 'jmax25', 'tpu25', 'rd25', 'alpha', 'theta',
      'dha_vcmax', 'topt_vcmax', 'dha_jmax', 'topt_jmax', 'dhd_jmax',
      'dha_tpu', 'topt_tpu', 'dhd_tpu'));

-- ── Stomatal Conductance: four selector-gated sub-model groups ──
UPDATE material_property_type
SET group_name = 'Ball-woodrow-berry', selector_property = 'stomatal_model', selector_value = 'BWB'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('bwb_gs0', 'bwb_a1'));

UPDATE material_property_type
SET group_name = 'Ball-berry-leuning', selector_property = 'stomatal_model', selector_value = 'BBL'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('bbl_gs0', 'bbl_a1', 'bbl_d0'));

UPDATE material_property_type
SET group_name = 'Medlyn Optimality', selector_property = 'stomatal_model', selector_value = 'Medlyn'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('medlyn_gs0', 'medlyn_g1'));

UPDATE material_property_type
SET group_name = 'Buckley-mott-farquhar', selector_property = 'stomatal_model', selector_value = 'BMF'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance')
  AND property_type_id IN (SELECT id FROM property_type WHERE property IN ('bmf_em', 'bmf_i0', 'bmf_k', 'bmf_b'));

-- ── Display labels (spec sheet col B/C). Display-only; property name is the key. ──
UPDATE material_property_type
SET label = CASE (SELECT property FROM property_type WHERE id = material_property_type.property_type_id)
    WHEN 'two_sided_heat_transfer' THEN 'Heat Transfer Flag'
    WHEN 'stomatal_sidedness'      THEN 'Stomatal Sidedness'
    WHEN 'vcmax25'    THEN 'V cmax25'
    WHEN 'jmax25'     THEN 'J max25'
    WHEN 'tpu25'      THEN 'TPU25'
    WHEN 'rd25'       THEN 'Rd25'
    WHEN 'alpha'      THEN 'Alpha'
    WHEN 'theta'      THEN 'Theta'
    WHEN 'dha_vcmax'  THEN 'dHa, V cmax'
    WHEN 'topt_vcmax' THEN 'T opt, V cmax'
    WHEN 'dha_jmax'   THEN 'dHa, J max'
    WHEN 'topt_jmax'  THEN 'T opt, J cmax'
    WHEN 'dhd_jmax'   THEN 'dHd, J max'
    WHEN 'dha_tpu'    THEN 'dha, TPU'
    WHEN 'topt_tpu'   THEN 'T opt, TPU'
    WHEN 'dhd_tpu'    THEN 'dhd, TPU'
    ELSE label
END
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis');

UPDATE material_property_type
SET label = CASE (SELECT property FROM property_type WHERE id = material_property_type.property_type_id)
    WHEN 'gamma_co2'      THEN 'Gamma_CO2'
    WHEN 'stomatal_model' THEN 'Stomatal Conductance'
    WHEN 'bwb_gs0'    THEN 'gs, o'
    WHEN 'bwb_a1'     THEN 'a1'
    WHEN 'bbl_gs0'    THEN 'gs, o'
    WHEN 'bbl_a1'     THEN 'a1'
    WHEN 'bbl_d0'     THEN 'Do'
    WHEN 'medlyn_gs0' THEN 'gs, o'
    WHEN 'medlyn_g1'  THEN 'g1'
    WHEN 'bmf_em'     THEN 'Em'
    WHEN 'bmf_i0'     THEN 'io'
    WHEN 'bmf_k'      THEN 'k'
    WHEN 'bmf_b'      THEN 'b'
    ELSE label
END
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Stomatal Conductance');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (27);
