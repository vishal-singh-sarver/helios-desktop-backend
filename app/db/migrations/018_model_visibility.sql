-- Migration 018 — model catalog + persisted geometry/model visibility
-- (Milestone 2, Revision 5).
--
-- Two independent levels of model enablement:
--   scenario_object_model  per-GEOMETRY: which models USE this geometry
--                          (the per-model dropdown of user story A10);
--                          scenario_object.render_enabled=0 excludes the
--                          geometry from all models (render icon).
--   scenario_model         per-SCENARIO run configuration: which models
--                          RUN when the user clicks the Run button.
-- Absent row = enabled in both tables (only explicit settings stored).
-- Effective participation of geometry G in model M:
--   scenario_model[M].enabled AND G.render_enabled AND G.models[M]
--
-- model_type is hierarchical: parent_id NULL = top-level model, otherwise
-- a submodel of its parent.
--
-- No triggers (runner splits on semicolons). updated_at is ORM-maintained.

CREATE TABLE IF NOT EXISTS model_type (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    model       TEXT NOT NULL COLLATE NOCASE,
    description TEXT,
    parent_id   INTEGER REFERENCES model_type(id) ON DELETE CASCADE,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (model, parent_id)
);

-- Plain UNIQUE(model, parent_id) treats NULL parents as distinct in SQLite,
-- so top-level names get their own partial unique index.
CREATE UNIQUE INDEX IF NOT EXISTS idx_model_type_toplevel_name_ci
    ON model_type(model) WHERE parent_id IS NULL;
CREATE INDEX IF NOT EXISTS idx_model_type_parent
    ON model_type(parent_id);

ALTER TABLE scenario_object ADD COLUMN visible        INTEGER NOT NULL DEFAULT 1;
ALTER TABLE scenario_object ADD COLUMN render_enabled INTEGER NOT NULL DEFAULT 1;

CREATE TABLE IF NOT EXISTS scenario_object_model (
    scenario_object_id INTEGER NOT NULL REFERENCES scenario_object(id) ON DELETE CASCADE,
    model_type_id      INTEGER NOT NULL REFERENCES model_type(id) ON DELETE RESTRICT,
    enabled            INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at         TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scenario_object_id, model_type_id)
);

CREATE TABLE IF NOT EXISTS scenario_model (
    scenario_id   TEXT NOT NULL REFERENCES scenarios(id) ON DELETE CASCADE,
    model_type_id INTEGER NOT NULL REFERENCES model_type(id) ON DELETE RESTRICT,
    enabled       INTEGER NOT NULL DEFAULT 1,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (scenario_id, model_type_id)
);

-- ── Seeds: top-level models ──

INSERT OR IGNORE INTO model_type (model, description) VALUES
    ('Radiation',                  'Radiation transport model'),
    ('Energy Balance',             'Surface energy balance model'),
    ('Solar Position',             'Sun position model'),
    ('Photosynthesis',             'Photosynthesis model'),
    ('Boundary Layer Conductance', 'Boundary layer conductance model'),
    ('Stomatal Conductance',       'Stomatal conductance model');

-- ── Seeds: submodels ──

WITH s(model, description, parent) AS (VALUES
    ('Farquhar',              'Farquhar photosynthesis submodel',          'Photosynthesis'),
    ('Pohlhausen',            'Pohlhausen boundary layer submodel',        'Boundary Layer Conductance'),
    ('InclinedPlate',         'Inclined plate boundary layer submodel',    'Boundary Layer Conductance'),
    ('Sphere',                'Sphere boundary layer submodel',            'Boundary Layer Conductance'),
    ('Ground',                'Ground boundary layer submodel',            'Boundary Layer Conductance'),
    ('Ball-Woodrow-Berry',    'Ball-Woodrow-Berry stomatal submodel',      'Stomatal Conductance'),
    ('Ball-Berry-Leuning',    'Ball-Berry-Leuning stomatal submodel',      'Stomatal Conductance'),
    ('Medlyn Optimality',     'Medlyn optimality stomatal submodel',       'Stomatal Conductance'),
    ('Buckley-Mott-Farquhar', 'Buckley-Mott-Farquhar stomatal submodel',   'Stomatal Conductance')
)
INSERT OR IGNORE INTO model_type (model, description, parent_id)
SELECT s.model, s.description, mt.id
FROM s JOIN model_type mt ON mt.model = s.parent AND mt.parent_id IS NULL;

INSERT OR IGNORE INTO schema_migrations(version) VALUES (18);
