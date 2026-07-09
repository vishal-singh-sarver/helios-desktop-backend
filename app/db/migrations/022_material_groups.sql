-- Migration 022 — MATERIAL GROUPS: materials are managed only inside named
-- groups; assignment to geometries is group-level; library→applied FK links
-- are removed (the "break point").
--
-- Structural changes:
--   (1) NEW material_group — the user-facing library object. GLOBAL: project_id
--       and scenario_id are nullable provenance (both ON DELETE SET NULL); the
--       name is globally unique (NOCASE), taking over project_material's old
--       namespace.
--   (2) project_material becomes a nameless group MEMBER: material_group_id
--       (NOT NULL, CASCADE) replaces project_id/scenario_id/name; one member
--       per material type per group (UNIQUE). The old UNIQUE(id,
--       material_type_id) is dropped — its only consumer was object_material's
--       composite FK, removed in (3).
--   (3) object_material becomes a service-maintained MATERIALIZED projection of
--       "groups assigned to a geometry x members of those groups". Its
--       project_material_id and new material_group_id are SOFT references (no
--       FK): deleting/updating library rows must NOT cascade into another
--       scenario's applied state — surviving orphan rows ARE the out-of-sync
--       state the material-sync API reports. sync moves to the assignment
--       table. UNIQUE(scenario_object_id, material_type_id) stays: it is the
--       DB-level enforcer of "no duplicate material type across the groups
--       assigned to one geometry".
--   (4) NEW object_material_group — the user-facing assignment (geometry x
--       group, one sync flag per pair). material_group_id is a soft reference
--       for the same reason.
--   object_property_data is UNTOUCHED: its composite FK still cascades frozen
--   snapshot rows when an object_material row is deleted (scenario-scoped
--   cleanup stays declarative).
--
-- DATA SAFETY (the 019 pattern): DROP TABLE performs an implicit DELETE that
-- FIRES ON DELETE CASCADE on children. Dropping the OLD object_material
-- cascades the frozen object_property_data rows, and dropping the OLD
-- project_material cascades material_data — so both children are backed up
-- into TEMP tables first and restored after the rebuilds. Whether a cascade
-- actually fired depends on the connection's FK pragma (and the pragma below
-- may be reset by autocommits between statements — do NOT rely on deferral;
-- the statement ORDER alone keeps every restored row's parent alive under
-- immediate FK enforcement), so the restore step clears survivors first
-- (idempotent under either FK state). Intrinsic object_property_data rows
-- (project_material_id IS NULL) are never cascaded and never leave the table.
--
-- Existing data: every material is wrapped into its own single-member group
-- with THE SAME id (group id = material id), carrying the material's name and
-- project/scenario provenance — including the mig-019 defaults, which become
-- global default groups. Old per-material assignment sync flags carry over as
-- the group-assignment flag. project_material keeps AUTOINCREMENT so member ids
-- are never reused: a stale soft reference can never re-bind to a new material.
--
-- The whole file runs on one connection inside engine.begin(); statements are
-- ';'-separated with no internal ';'.

PRAGMA defer_foreign_keys=ON;

-- ── (1) material_group: one group per existing material (group id = material id) ──

CREATE TABLE material_group (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id  TEXT REFERENCES projects(id)  ON DELETE SET NULL,
    scenario_id TEXT REFERENCES scenarios(id) ON DELETE SET NULL,
    name        TEXT NOT NULL COLLATE NOCASE
                    CHECK (length(name) BETWEEN 1 AND 20),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX idx_material_group_name_ci
    ON material_group(name COLLATE NOCASE);
CREATE INDEX idx_material_group_project
    ON material_group(project_id);

INSERT INTO material_group (id, project_id, scenario_id, name, created_at, updated_at)
SELECT id, project_id, scenario_id, name, created_at, updated_at
FROM project_material;

-- ── (2) Back up the cascade children BEFORE the rebuilds drop them ──

CREATE TEMP TABLE _mig022_material_data AS
    SELECT * FROM material_data;
CREATE TEMP TABLE _mig022_object_material AS
    SELECT * FROM object_material;
CREATE TEMP TABLE _mig022_opd_frozen AS
    SELECT * FROM object_property_data WHERE project_material_id IS NOT NULL;

-- ── (3) Rebuild object_material FIRST (composite FK to project_material must
--        be gone before project_material itself is rebuilt) ──

CREATE TABLE object_material_new (
    scenario_object_id  INTEGER NOT NULL REFERENCES scenario_object(id) ON DELETE CASCADE,
    project_material_id INTEGER NOT NULL,
    material_group_id   INTEGER NOT NULL,
    material_type_id    INTEGER NOT NULL REFERENCES material_type(id) ON DELETE RESTRICT,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scenario_object_id, project_material_id),
    UNIQUE (scenario_object_id, material_type_id)
);

INSERT INTO object_material_new
    (scenario_object_id, project_material_id, material_group_id,
     material_type_id, created_at, updated_at)
SELECT scenario_object_id, project_material_id, project_material_id,
       material_type_id, created_at, updated_at
FROM object_material;

DROP TABLE object_material;

ALTER TABLE object_material_new RENAME TO object_material;

CREATE INDEX idx_object_material_material
    ON object_material(project_material_id);
CREATE INDEX idx_object_material_group
    ON object_material(scenario_object_id, material_group_id);

-- ── (4) Rebuild project_material as a nameless group member ──

CREATE TABLE project_material_new (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    material_group_id INTEGER NOT NULL REFERENCES material_group(id) ON DELETE CASCADE,
    material_type_id  INTEGER NOT NULL REFERENCES material_type(id) ON DELETE RESTRICT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (material_group_id, material_type_id)
);

INSERT INTO project_material_new
    (id, material_group_id, material_type_id, created_at, updated_at)
SELECT id, id, material_type_id, created_at, updated_at
FROM project_material;

DROP TABLE project_material;

ALTER TABLE project_material_new RENAME TO project_material;

CREATE INDEX idx_project_material_group
    ON project_material(material_group_id);

-- ── (5) Defensive clear + restore of the cascade children (019:78-84 pattern;
--        child-first delete order, parent-first insert order) ──

DELETE FROM object_property_data WHERE project_material_id IS NOT NULL;
DELETE FROM material_data;

INSERT INTO material_data SELECT * FROM _mig022_material_data;
INSERT INTO object_property_data SELECT * FROM _mig022_opd_frozen;

-- ── (6) object_material_group: the user-facing assignment. Seed one group
--        assignment per old material assignment (group id = material id);
--        the per-material sync flag carries over as the group flag ──

CREATE TABLE object_material_group (
    scenario_object_id INTEGER NOT NULL REFERENCES scenario_object(id) ON DELETE CASCADE,
    material_group_id  INTEGER NOT NULL,
    sync               INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scenario_object_id, material_group_id)
);

CREATE INDEX idx_omg_group
    ON object_material_group(material_group_id);

INSERT INTO object_material_group
    (scenario_object_id, material_group_id, sync, created_at, updated_at)
SELECT scenario_object_id, project_material_id, sync, created_at, updated_at
FROM _mig022_object_material;

-- ── (7) Cleanup ──

DROP TABLE _mig022_material_data;
DROP TABLE _mig022_object_material;
DROP TABLE _mig022_opd_frozen;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (22);
