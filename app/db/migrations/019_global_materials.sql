-- Migration 019 — ctx_object_id, per-scenario object names, GLOBAL materials,
-- default materials.
--
-- Three structural changes + default-material seeds:
--   (1) scenario_object.ctx_object_id — the Helios context object id returned
--       by addTileObject/addBoxObject/... (nullable; session-scoped at runtime).
--   (2) Object-name uniqueness moves from per-PROJECT to per-SCENARIO: a name
--       may now repeat across scenarios of a project but not within one.
--   (3) project_material becomes GLOBAL: project_id becomes nullable (NULL = an
--       app-shipped default / a material not bound to a project) and name is
--       globally unique (one flat namespace, decision #3).
--
-- DATA SAFETY (the dangerous part):
-- SQLite cannot drop NOT NULL via ALTER, so project_material must be rebuilt
-- (create _new, copy, drop, rename — the migration-010 precedent). BUT a
-- DROP TABLE performs an implicit DELETE that FIRES ON DELETE CASCADE on the
-- children (material_data, object_material, and transitively the frozen rows of
-- object_property_data). PRAGMA defer_foreign_keys=ON only defers the constraint
-- CHECK to commit time — it does NOT suppress the CASCADE action. So we back the
-- children up into TEMP tables before the drop and restore them after the rename.
-- object_property_data INTRINSIC rows (project_material_id IS NULL) are NOT
-- cascaded (composite FK with a NULL column isn't enforced) and so survive
-- untouched.
--
-- The whole file runs on one connection inside engine.begin(), so the PRAGMA
-- persists across every statement. Statements are ';'-separated and contain no
-- internal ';' (no triggers); window functions require SQLite >= 3.25.

PRAGMA defer_foreign_keys=ON;

-- ── (1) scenario_object.ctx_object_id (additive; runner tolerates re-run) ──
ALTER TABLE scenario_object ADD COLUMN ctx_object_id INTEGER;

-- ── (2) Object-name uniqueness: per-PROJECT -> per-SCENARIO ──
DROP INDEX IF EXISTS idx_scenario_object_project_name_ci;
CREATE UNIQUE INDEX IF NOT EXISTS idx_scenario_object_scenario_name_ci
    ON scenario_object(scenario_id, name COLLATE NOCASE);

-- ── (3) project_material -> GLOBAL ──

-- 3a. Back up the cascade children BEFORE the rebuild drops them.
CREATE TEMP TABLE _mig019_material_data AS
    SELECT * FROM material_data;
CREATE TEMP TABLE _mig019_object_material AS
    SELECT * FROM object_material;
CREATE TEMP TABLE _mig019_opd_frozen AS
    SELECT * FROM object_property_data WHERE project_material_id IS NOT NULL;

-- 3b. Rebuild project_material with a nullable project_id. The composite
--     UNIQUE(id, material_type_id) MUST be preserved — object_material's FK
--     targets it.
CREATE TABLE project_material_new (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id       TEXT REFERENCES projects(id) ON DELETE CASCADE,
    scenario_id      TEXT REFERENCES scenarios(id) ON DELETE SET NULL,
    material_type_id INTEGER NOT NULL REFERENCES material_type(id) ON DELETE RESTRICT,
    name             TEXT NOT NULL COLLATE NOCASE
                         CHECK (length(name) BETWEEN 1 AND 20),
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at       TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (id, material_type_id)
);

INSERT INTO project_material_new
    (id, project_id, scenario_id, material_type_id, name, created_at, updated_at)
SELECT id, project_id, scenario_id, material_type_id, name, created_at, updated_at
FROM project_material;

DROP TABLE project_material;

ALTER TABLE project_material_new RENAME TO project_material;

-- 3c. Restore the children. Whether the DROP above cascade-emptied them depends
-- on the connection's foreign_keys pragma (defer_foreign_keys only defers the
-- CHECK, and a non-app connection may have FK off entirely), so clear any
-- survivors first, then restore exactly the backed-up rows. Idempotent under
-- either FK state. Child-first delete order, parent-first insert order.
DELETE FROM object_property_data WHERE project_material_id IS NOT NULL;
DELETE FROM object_material;
DELETE FROM material_data;

INSERT INTO material_data SELECT * FROM _mig019_material_data;
INSERT INTO object_material SELECT * FROM _mig019_object_material;
INSERT INTO object_property_data SELECT * FROM _mig019_opd_frozen;

DROP TABLE _mig019_material_data;
DROP TABLE _mig019_object_material;
DROP TABLE _mig019_opd_frozen;

-- 3d. De-dup names BEFORE the global unique index (existing DBs may hold the
--     same name in two projects — e.g. auto-named 'Material.001'). Keep one row
--     per NOCASE name (prefer a global/NULL-project row, else lowest id); rename
--     the rest to '<base>-<id>', truncating the base so total length stays <= 20.
UPDATE project_material
SET name = substr(name, 1, max(1, 20 - length('-' || id))) || '-' || id
WHERE id IN (
    SELECT id FROM (
        SELECT id,
               ROW_NUMBER() OVER (
                   PARTITION BY name COLLATE NOCASE
                   ORDER BY (project_id IS NOT NULL), id
               ) AS rn
        FROM project_material
    ) WHERE rn > 1
);

-- 3e. Recreate the non-unique project index; replace the per-project name index
--     with a GLOBAL unique one.
CREATE INDEX IF NOT EXISTS idx_project_material_project
    ON project_material(project_id);
DROP INDEX IF EXISTS idx_project_material_project_name_ci;

-- ── (4) Seed DEFAULT global materials (project_id/scenario_id NULL) ──
-- One per material_type. Guarded by NOT EXISTS so a pre-existing material with
-- the same name (only possible on a populated DB) never creates a duplicate that
-- would break the unique index below.
INSERT INTO project_material (project_id, scenario_id, material_type_id, name)
SELECT NULL, NULL, d.material_type_id, d.dname
FROM (
    SELECT mt.id AS material_type_id,
           CASE mt.materialtype
               WHEN 'Radiation'                  THEN 'Default Radiation'
               WHEN 'Energy Balance'             THEN 'Default Energy Bal'
               WHEN 'Solar Position'             THEN 'Default Solar Pos'
               WHEN 'Photosynthesis'             THEN 'Default Photosyn'
               WHEN 'Boundary Layer Conductance' THEN 'Default Boundary Lyr'
               WHEN 'Stomatal Conductance'       THEN 'Default Stomatal'
               ELSE 'Default ' || mt.id
           END AS dname
    FROM material_type mt
) d
WHERE NOT EXISTS (
    SELECT 1 FROM project_material pm WHERE pm.name = d.dname COLLATE NOCASE
);

-- Now the namespace is collision-free: create the global unique name index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_project_material_name_ci
    ON project_material(name COLLATE NOCASE);

-- 4b. Seed visualisation defaults (neutral grey, no texture) for every default
--     material. Canonical TEXT encoding: 0..255 integers as base-10 strings.
INSERT OR IGNORE INTO material_data (project_material_id, property_type_id, value)
SELECT pm.id, pt.id,
       CASE pt.property
           WHEN 'color_r' THEN '128'
           WHEN 'color_g' THEN '128'
           WHEN 'color_b' THEN '128'
       END
FROM project_material pm
CROSS JOIN property_type pt
WHERE pm.project_id IS NULL AND pm.scenario_id IS NULL
  AND pt.property IN ('color_r', 'color_g', 'color_b');

INSERT OR IGNORE INTO schema_migrations(version) VALUES (19);
