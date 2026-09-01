-- Migration 024 — VISUALISER material type: sole owner of the visualisation
-- properties (color_r, color_g, color_b, opacity, texture_file).
--
-- Story ("Plan B"): colour / opacity / texture is a RENDERING concern, not a
-- physical-model concern. Migration 017 attached the visualisation properties to
-- ALL six model material types via a CROSS JOIN (017:300-312). This migration
-- introduces a seventh material type, "Visualiser", makes it the SOLE owner of
-- the visualisation properties, removes those links from the other six types,
-- and adds a new "opacity" property (RGBA alpha expressed as a 0-100 percent;
-- material_apply divides it by 100 to get the colour's alpha channel, which was
-- previously hard-coded to fully opaque).
--
-- Additive + one leaf DELETE — no table/schema change, no rebuild, no FK
-- deferral needed:
--   (a) INSERT the Visualiser material_type (INSERT OR IGNORE on the UNIQUE
--       materialtype name).
--   (b) INSERT the new "opacity" property_type: integer percent 0..100, stored
--       as a base-10 string; material_apply divides by 100 for the 0..1 alpha.
--       INSERT OR IGNORE on the UNIQUE property name.
--   (c) Map the five visualisation properties onto Visualiser (display_order
--       90-94: color_r, color_g, color_b, opacity, texture_file). INSERT OR
--       IGNORE on material_property_type's UNIQUE(material_type_id,
--       property_type_id).
--   (d) DELETE the four ORIGINAL visualisation mappings (color_r, color_g,
--       color_b, texture_file) from the OTHER six types. opacity was never on
--       those types, so it is not listed here. material_property_type is a leaf
--       link table (nothing FK-references it), so this is FK-safe; material_data
--       rows are untouched, so the change is reversible. Idempotent: a re-run
--       deletes zero rows.
--   (e) Seed a global "Default Visualiser" material_group (project_id /
--       scenario_id NULL, like the other six wrapped defaults), its single
--       Visualiser member, neutral grey-128 colour and opacity 100 (fully
--       opaque) — so the library ships one more default group
--       (DEFAULT_GROUP_COUNT 6 -> 7). The group->member->material_data chain is
--       linked by pure SQL subqueries (no host variables). The group INSERT is
--       guarded by NOT EXISTS so a re-run never violates the UNIQUE(name) index
--       (019:114-134 pattern).
--
-- Visualiser deliberately gets NO model_type row (migration 018): it is a
-- rendering type, not a physics model, and must not appear in the model list.
--
-- The whole file runs on one connection inside the migration runner's
-- transaction; statements are ';'-separated with no internal ';' and only
-- full-line '--' comments (db/database.py _split_statements).

-- ── (a) Visualiser material type ──

INSERT OR IGNORE INTO material_type (materialtype, description) VALUES
    ('Visualiser', 'Visualisation colour, opacity and texture for scene rendering');

-- ── (b) New opacity property (RGBA alpha as an integer percent 0..100) ──

INSERT OR IGNORE INTO property_type (property, description, datatype_id, min, max)
SELECT 'opacity', 'Visualisation opacity (percent, 0-100)',
       (SELECT id FROM datatype WHERE name = 'integer'),
       0, 100;

-- ── (c) Visualisation properties -> Visualiser (display_order 90-94) ──

INSERT OR IGNORE INTO material_property_type (material_type_id, property_type_id, display_order)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Visualiser'),
       pt.id,
       CASE pt.property
           WHEN 'color_r'      THEN 90
           WHEN 'color_g'      THEN 91
           WHEN 'color_b'      THEN 92
           WHEN 'opacity'      THEN 93
           ELSE 94
       END
FROM property_type pt
WHERE pt.property IN ('color_r', 'color_g', 'color_b', 'opacity', 'texture_file');

-- ── (d) Remove the original visualisation mappings from the OTHER six types ──

DELETE FROM material_property_type
WHERE property_type_id IN (
        SELECT id FROM property_type
        WHERE property IN ('color_r', 'color_g', 'color_b', 'texture_file')
    )
  AND material_type_id <> (SELECT id FROM material_type WHERE materialtype = 'Visualiser');

-- ── (e) Default Visualiser group + member + grey-128 colour + opacity 100 ──

INSERT INTO material_group (project_id, scenario_id, name)
SELECT NULL, NULL, 'Default Visualiser'
WHERE NOT EXISTS (
    SELECT 1 FROM material_group WHERE name = 'Default Visualiser' COLLATE NOCASE
);

INSERT OR IGNORE INTO project_material (material_group_id, material_type_id)
SELECT (SELECT id FROM material_group WHERE name = 'Default Visualiser' COLLATE NOCASE),
       (SELECT id FROM material_type WHERE materialtype = 'Visualiser');

INSERT OR IGNORE INTO material_data (project_material_id, property_type_id, value)
SELECT pm.id, pt.id,
       CASE pt.property
           WHEN 'color_r' THEN '128'
           WHEN 'color_g' THEN '128'
           WHEN 'color_b' THEN '128'
           WHEN 'opacity' THEN '100'
       END
FROM project_material pm
JOIN material_group mg ON mg.id = pm.material_group_id
JOIN material_type mt ON mt.id = pm.material_type_id
CROSS JOIN property_type pt
WHERE mg.name = 'Default Visualiser' COLLATE NOCASE
  AND mt.materialtype = 'Visualiser'
  AND pt.property IN ('color_r', 'color_g', 'color_b', 'opacity');

-- ── (f) Self-register ──

INSERT OR IGNORE INTO schema_migrations(version) VALUES (24);
