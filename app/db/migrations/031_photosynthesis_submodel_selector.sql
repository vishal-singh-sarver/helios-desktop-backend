-- Migration 031 — PHOTOSYNTHESIS SUBMODEL: gate the Farquhar parameter group
-- behind a `submodel` enum selector.
--
-- Story: Photosynthesis rendered its 14 Farquhar coefficients as a plain
-- collapsible group (migration 027), with nothing above it naming the model they
-- belong to. Helios itself ships three photosynthesis sub-models
-- (setModelType_Empirical / _Farquhar / _C4); the GUI models only Farquhar
-- today. Making the group selector-gated — exactly like the four Stomatal
-- Conductance sub-models in 027 — means a second sub-model later costs one enum
-- token plus one group, not a re-shape of the form.
--
-- The GROUP NAME becomes the dropdown option: materialBlueprint builds a
-- selector's option labels from the group names it gates, so 'farquhar_model'
-- reads "Farquhar model" with nothing else to seed. Note the group also stops
-- rendering its own collapsible header once it is gated (the dropdown names it
-- instead) — that is the same shape Stomatal Conductance already has.
--
-- Backfill: a gated property is live only while the member's STORED selector
-- value matches (eav_validation.member_property_values, and the identical rule
-- in the form). Existing Photosynthesis members have no `submodel` value, so
-- without a backfill the group vanishes, the form's clear-inactive-groups effect
-- blanks the 14 coefficients, and the next PUT — full-replacement semantics —
-- deletes them from material_data. BOTH value tables are therefore seeded:
-- material_data (the library member) and object_property_data (the frozen
-- per-object snapshot, which is also the other side of scene_object_service's
-- library_drift comparison; seeding only the first would make every existing
-- Photosynthesis assignment report spurious drift).
--
-- Append-only; idempotent: every INSERT is OR IGNORE against an existing unique
-- constraint (property_type.property, material_property_type
-- (material_type_id, property_type_id), material_data
-- (project_material_id, property_type_id), idx_opd_frozen) and the one UPDATE
-- assigns constants, so a drift re-run changes nothing.

-- ── (a) The selector property ──

INSERT OR IGNORE INTO property_type (property, description, datatype_id, enum_values) VALUES
    ('submodel', 'Photosynthesis sub-model',
        (SELECT id FROM datatype WHERE name = 'enum'),
        '["farquhar_model"]');

-- ── (b) Link it to Photosynthesis: editable, TOP-LEVEL (no group_name) ──
-- display_order 7 puts it directly after stomatal_sidedness(6) and immediately
-- above the group, mirroring how stomatal_model(10) sits before its groups. It
-- ties with vcmax25(7), which is harmless: _material_type_payload partitions
-- top-level properties and group members into separate lists, so the two are
-- never ordered against one another.

INSERT OR IGNORE INTO material_property_type
    (material_type_id, property_type_id, display_order, visibility, label)
SELECT (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis'),
       pt.id, 7, 'editable', 'Photosynthesis Model'
FROM property_type pt
WHERE pt.property = 'submodel';

-- ── (c) Gate the Farquhar group on it ──
-- Keyed on group_name rather than a re-listed set of 14 property names, so it
-- cannot drift from the group 027 established.

UPDATE material_property_type
SET selector_property = 'submodel', selector_value = 'farquhar_model'
WHERE material_type_id = (SELECT id FROM material_type WHERE materialtype = 'Photosynthesis')
  AND group_name = 'Farquhar model';

-- ── (d) Backfill the library members ──

INSERT OR IGNORE INTO material_data (project_material_id, property_type_id, value)
SELECT pm.id,
       (SELECT id FROM property_type WHERE property = 'submodel'),
       'farquhar_model'
FROM project_material pm
JOIN material_type mt ON mt.id = pm.material_type_id
WHERE mt.materialtype = 'Photosynthesis';

-- ── (e) Backfill the frozen per-object snapshots ──
-- Driven off object_material, not project_material, so stale rows (member
-- deleted from the library but still painting until the scenario syncs) are
-- covered too, and the composite FK to object_material is satisfied by
-- construction.

INSERT OR IGNORE INTO object_property_data
    (scenario_object_id, project_material_id, property_type_id, value)
SELECT om.scenario_object_id, om.project_material_id,
       (SELECT id FROM property_type WHERE property = 'submodel'),
       'farquhar_model'
FROM object_material om
JOIN material_type mt ON mt.id = om.material_type_id
WHERE mt.materialtype = 'Photosynthesis';

INSERT OR IGNORE INTO schema_migrations(version) VALUES (31);
