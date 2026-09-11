-- Migration 032 — remove the seven seeded DEFAULT material groups.
--
-- Story: 019 seeded six "Default <type>" materials (022 wrapped them into
-- groups without re-seeding) and 024 added a seventh, "Default Visualiser".
-- They were the library's starter content. Every new ground now gets its own
-- 'mtl.<ground name>' Visualiser group instead, so the seven are dead weight in
-- a GLOBAL, uniquely-named namespace: they occupy names and clutter the
-- Materials panel. Nothing in app code looks any of them up by name.
--
-- Forward-only. The runner has no rollback, so this is a new migration rather
-- than an edit to 019/024 — both are already applied everywhere, and the staged
-- migration tests use their seeds as realistic data-migration fixtures. A fresh
-- install still creates the seven and deletes them moments later.
--
-- ASSIGNED defaults are unassigned too. Deleting only the library rows is what
-- the app's own DELETE /groups/{id} does, and it deliberately leaves the applied
-- rows behind as STALE — they keep painting the geometry and keep owning their
-- material-type slot until each scenario syncs. A migration the user cannot see
-- running must not leave that behind, so the assignments and their frozen
-- snapshots go in the same sweep. A geometry that carried one is left with no
-- material and builds 'plain'.
--
-- Every DELETE is EXPLICIT rather than leaning on ON DELETE CASCADE: the
-- migration-test engine (tests/test_migrations.py temp_engine) is a bare
-- create_engine() and never gets the app engine's PRAGMA foreign_keys=ON
-- listener (db/database.py), so cascades do not fire there. Explicit deletes
-- behave the same either way. Children before parents, so each subquery still
-- resolves against rows that are still present.
--
-- Matching is by exact seeded NAME plus NULL provenance:
--   * There is no is_default flag and the seed ids are not fixed (019 assigned
--     them by AUTOINCREMENT after whatever user materials already existed), so
--     the name is the only key available. A default the user RENAMED is
--     deliberately left alone — it became their material.
--   * NULL provenance does NOT by itself mean "seeded": the Materials panel
--     creates groups with a name and nothing else, so user groups are NULL/NULL
--     too. It is a cheap extra guard that spares any same-named group carrying
--     provenance (every 'mtl.*' group does).
--
-- The whole file runs on one connection inside the migration runner's
-- transaction; statements are ';'-separated with no internal ';' and only
-- full-line '--' comments (db/database.py _split_statements).

-- ── (a) Frozen per-geometry snapshots of the defaults' members ──

DELETE FROM object_property_data
WHERE project_material_id IN (
    SELECT pm.id FROM project_material pm
    JOIN material_group mg ON mg.id = pm.material_group_id
    WHERE mg.project_id IS NULL AND mg.scenario_id IS NULL
      AND mg.name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
                      'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
                      'Default Visualiser')
);

-- ── (b) Applied rows and the assignments that own them ──

DELETE FROM object_material
WHERE material_group_id IN (
    SELECT id FROM material_group
    WHERE project_id IS NULL AND scenario_id IS NULL
      AND name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
                   'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
                   'Default Visualiser')
);

DELETE FROM object_material_group
WHERE material_group_id IN (
    SELECT id FROM material_group
    WHERE project_id IS NULL AND scenario_id IS NULL
      AND name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
                   'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
                   'Default Visualiser')
);

-- ── (c) The library rows: values, members, then the groups themselves ──

DELETE FROM material_data
WHERE project_material_id IN (
    SELECT pm.id FROM project_material pm
    JOIN material_group mg ON mg.id = pm.material_group_id
    WHERE mg.project_id IS NULL AND mg.scenario_id IS NULL
      AND mg.name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
                      'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
                      'Default Visualiser')
);

DELETE FROM project_material
WHERE material_group_id IN (
    SELECT id FROM material_group
    WHERE project_id IS NULL AND scenario_id IS NULL
      AND name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
                   'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
                   'Default Visualiser')
);

DELETE FROM material_group
WHERE project_id IS NULL AND scenario_id IS NULL
  AND name IN ('Default Radiation', 'Default Energy Bal', 'Default Solar Pos',
               'Default Photosyn', 'Default Boundary Lyr', 'Default Stomatal',
               'Default Visualiser');

-- ── (d) Self-register ──

INSERT OR IGNORE INTO schema_migrations(version) VALUES (32);
