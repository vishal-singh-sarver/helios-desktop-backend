-- Migration 025 — VISUALISER texture_toggle: the mode selector.
--
-- Story: a Visualiser material is EITHER a solid colour OR a texture, never
-- both. `texture_toggle` (boolean) is the explicit mode selector that drives
-- required-by-mode validation on writes:
--     texture_toggle = 1 (true)  -> texture mode : texture_file required,
--                                    colour fields must be absent.
--     texture_toggle = 0 (false) -> colour mode  : color_r/g/b + opacity
--                                    required, texture_file must be absent.
-- The apply path reads it to decide what to paint (texture vs untextured colour).
--
-- Additive only — no schema change, no FK risk:
--   (a) INSERT the boolean `texture_toggle` property_type.
--   (b) Map it onto Visualiser (display_order 89 — the selector shown first).
--   (c) Seed the Default Visualiser member to colour mode (toggle = '0'); it
--       already carries grey-128 colour + opacity 100 (migration 024), so this
--       makes it a complete, valid colour-mode member.
--
-- The whole file runs on one connection inside the migration runner's
-- transaction; statements are ';'-separated with no internal ';' and only
-- full-line '--' comments (db/database.py _split_statements).

-- ── (a) texture_toggle property (boolean; no min/max) ──

INSERT OR IGNORE INTO property_type (property, description, datatype_id, min, max)
SELECT 'texture_toggle',
       'Visualiser mode selector: true = texture, false = solid colour',
       (SELECT id FROM datatype WHERE name = 'boolean'),
       NULL, NULL;

-- ── (b) Map texture_toggle onto Visualiser (display_order 89, before the fields) ──

INSERT OR IGNORE INTO material_property_type (material_type_id, property_type_id, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Visualiser'),
       (SELECT id FROM property_type WHERE property = 'texture_toggle'),
       89;

-- ── (c) Seed the Default Visualiser member to colour mode (toggle = false) ──

INSERT OR IGNORE INTO material_data (project_material_id, property_type_id, value)
SELECT pm.id,
       (SELECT id FROM property_type WHERE property = 'texture_toggle'),
       '0'
FROM project_material pm
JOIN material_group mg ON mg.id = pm.material_group_id
JOIN material_type mt ON mt.id = pm.material_type_id
WHERE mg.name = 'Default Visualiser' COLLATE NOCASE
  AND mt.materialtype = 'Visualiser';

-- ── (d) Self-register ──

INSERT OR IGNORE INTO schema_migrations(version) VALUES (25);
