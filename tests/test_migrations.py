"""
Tests for the resilient migration runner in app/db/database.py.

The runner must roll an older/drifted database forward without crashing —
the state left by an app update or a reinstall that kept the existing
backend-data/ DB, where the schema is ahead of the recorded versions.
"""
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text

import app.db.database as database


@pytest.fixture
def temp_engine(tmp_path, monkeypatch):
    """Point run_migrations() at a throwaway SQLite file for this test."""
    db_file = tmp_path / "test.db"
    eng = create_engine(
        f"sqlite:///{db_file}", connect_args={"check_same_thread": False}
    )
    monkeypatch.setattr(database, "engine", eng)
    yield eng
    eng.dispose()


def _versions(eng):
    with eng.begin() as conn:
        return {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}


def _columns(eng, table):
    with eng.begin() as conn:
        return {r[1] for r in conn.execute(text(f'PRAGMA table_info("{table}")'))}


def _apply_through(eng, max_version):
    """Apply every not-yet-recorded migration with version <= max_version
    (mimics the runner), leaving later ones pending. Skipping recorded versions
    makes staged calls safe (e.g. _apply_through(…, 18) → seed → …(…, 21))."""
    mig_dir = Path(database.__file__).parent / "migrations"
    with eng.begin() as conn:
        conn.execute(text(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now')))"
        ))
        applied = {r[0] for r in conn.execute(text("SELECT version FROM schema_migrations"))}
    for f in sorted(mig_dir.glob("*.sql")):
        v = int(f.stem.split("_")[0])
        if v > max_version or v in applied:
            continue
        with eng.begin() as conn:
            for stmt in database._split_statements(f.read_text(encoding="utf-8")):
                conn.execute(text(stmt))
            conn.execute(
                text("INSERT OR IGNORE INTO schema_migrations(version) VALUES (:v)"), {"v": v}
            )


def test_019_preserves_data_and_globalises_materials(temp_engine):
    """019 rebuilds project_material (project_id -> nullable, global-unique name).
    The rebuild DROPs the table, whose ON DELETE CASCADE would wipe material_data
    / object_material / frozen object_property_data — so the children are backed
    up and restored. Verify a populated DB survives, duplicate names de-dup, the
    defaults seed, the cascade still fires, and the rebuild never wipes data.

    PINNED to the pre-022 era (migration 022 reshapes these tables again, so the
    post-conditions here only hold at version 21); the 019→022 handover is
    covered by test_022_wraps_materials_into_groups."""
    _apply_through(temp_engine, 18)
    with temp_engine.begin() as c:
        rad = c.execute(text("SELECT id FROM material_type WHERE materialtype='Radiation'")).scalar()
        cr = c.execute(text("SELECT id FROM property_type WHERE property='color_r'")).scalar()
        ln = c.execute(text("SELECT id FROM property_type WHERE property='length'")).scalar()
        grd = c.execute(text("SELECT id FROM object_types WHERE object='Ground'")).scalar()
        c.execute(text("INSERT INTO projects(id,session_id,name) VALUES('p1','s1','P1'),('p2','s1','P2')"))
        c.execute(text("INSERT INTO scenarios(id,project_id,name) VALUES('sc1','p1','Main')"))
        # Cross-project duplicate name (case-insensitive) — must be de-duped.
        c.execute(text("INSERT INTO project_material(id,project_id,material_type_id,name) "
                       "VALUES (1,'p1',:r,'KeepMe'),(2,'p2',:r,'keepme')"), {"r": rad})
        c.execute(text("INSERT INTO material_data(project_material_id,property_type_id,value) "
                       "VALUES (1,:cr,'200')"), {"cr": cr})
        c.execute(text("INSERT INTO scenario_object(id,scenario_id,project_id,name,object_type_id,helios_uuids) "
                       "VALUES (1,'sc1','p1','Ground',:g,'[]')"), {"g": grd})
        c.execute(text("INSERT INTO object_material(scenario_object_id,project_material_id,material_type_id,sync) "
                       "VALUES (1,1,:r,0)"), {"r": rad})
        c.execute(text("INSERT INTO object_property_data(scenario_object_id,project_material_id,property_type_id,value) "
                       "VALUES (1,1,:cr,'200')"), {"cr": cr})   # frozen
        c.execute(text("INSERT INTO object_property_data(scenario_object_id,project_material_id,property_type_id,value) "
                       "VALUES (1,NULL,:ln,'5')"), {"ln": ln})  # intrinsic

    _apply_through(temp_engine, 21)   # applies 019 (pre-022 era) on the populated DB

    assert 19 in _versions(temp_engine)
    assert "ctx_object_id" in _columns(temp_engine, "scenario_object")
    with temp_engine.begin() as c:
        info = c.execute(text("PRAGMA table_info('project_material')")).fetchall()
        assert next(r[3] for r in info if r[1] == "project_id") == 0   # project_id nullable
        # Children preserved through the destructive rebuild (the seeded defaults
        # also add color material_data, so check the specific pre-existing row).
        assert c.execute(text("SELECT value FROM material_data "
                              "WHERE project_material_id=1 AND property_type_id="
                              "(SELECT id FROM property_type WHERE property='color_r')")).scalar() == "200"
        assert c.execute(text("SELECT count(*) FROM object_material")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM object_property_data "
                              "WHERE project_material_id IS NOT NULL")).scalar() == 1
        assert c.execute(text("SELECT count(*) FROM object_property_data "
                              "WHERE project_material_id IS NULL")).scalar() == 1
        # One default material per material_type seeded (6 types).
        assert c.execute(text("SELECT count(*) FROM project_material WHERE project_id IS NULL")).scalar() == 6
        # Duplicate name de-duped; the lower-id row keeps the original name.
        names = dict(c.execute(text("SELECT id,name FROM project_material WHERE id IN (1,2)")).fetchall())
        assert names[1] == "KeepMe"
        assert names[2].lower() != "keepme"
        assert c.execute(text("SELECT count(*) FROM sqlite_master WHERE type='index' "
                              "AND name='idx_project_material_name_ci'")).scalar() == 1
    # The composite-FK cascade still fires after the rebuild. temp_engine has no
    # foreign_keys=ON connect listener, so enable it on a raw connection for the
    # check (PRAGMA must run outside a transaction).
    raw = temp_engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("DELETE FROM project_material WHERE id=1")
        raw.commit()
        cur.execute("SELECT count(*) FROM object_material")
        assert cur.fetchone()[0] == 0
    finally:
        raw.close()


def test_019_guard_covers_post_022_schema(temp_engine):
    """The widened 019 guard: on a post-022 schema (project_material has
    material_group_id, project_id is GONE) with v19 unrecorded (history drift),
    the runner must stamp 19 and NOT re-run 019 — an unguarded re-run crashes at
    startup on `no such column: project_id` (crash loop, exit 3)."""
    database.run_migrations()   # full, current DB (through 022)
    with temp_engine.begin() as conn:
        rad = conn.execute(text("SELECT id FROM material_type LIMIT 1")).scalar()
        conn.execute(text("INSERT INTO material_group(id,name) VALUES(500,'KeepDrift')"))
        conn.execute(text("INSERT INTO project_material(material_group_id,material_type_id) "
                          "VALUES(500,:r)"), {"r": rad})
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 19"))

    database.run_migrations()   # must NOT crash / re-run the rebuild

    with temp_engine.begin() as conn:
        assert conn.execute(
            text("SELECT count(*) FROM material_group WHERE name='KeepDrift'")
        ).scalar() == 1
    assert 19 in _versions(temp_engine)


def test_022_guard_skips_destructive_rebuild(temp_engine):
    """If project_material already has material_group_id but v22 is unrecorded
    (history drift), the runner must stamp 22 and NOT re-run the rebuild —
    re-running crashes on the dropped project_id column."""
    database.run_migrations()
    with temp_engine.begin() as conn:
        defaults = conn.execute(text("SELECT count(*) FROM material_group")).scalar()
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 22"))

    database.run_migrations()   # must NOT crash

    assert 22 in _versions(temp_engine)
    with temp_engine.begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM material_group")).scalar() == defaults


def test_022_wraps_materials_into_groups(temp_engine):
    """022 on a populated pre-022 DB: every material becomes a group with the
    SAME id (name + provenance carried, defaults included); project_material is
    reshaped into nameless members; object_material is rebuilt with the group
    attribution column and without sync; assignments become
    object_material_group rows (sync carried over); material_data and BOTH
    kinds of object_property_data rows survive; and the library→applied FK
    links are gone (the break point)."""
    _apply_through(temp_engine, 21)
    with temp_engine.begin() as c:
        rad = c.execute(text("SELECT id FROM material_type WHERE materialtype='Radiation'")).scalar()
        cr = c.execute(text("SELECT id FROM property_type WHERE property='color_r'")).scalar()
        ln = c.execute(text("SELECT id FROM property_type WHERE property='length'")).scalar()
        grd = c.execute(text("SELECT id FROM object_types WHERE object='Ground'")).scalar()
        c.execute(text("INSERT INTO projects(id,session_id,name) VALUES('p1','s1','P1')"))
        c.execute(text("INSERT INTO scenarios(id,project_id,name) VALUES('sc1','p1','Main')"))
        c.execute(text("INSERT INTO project_material(id,project_id,scenario_id,material_type_id,name) "
                       "VALUES (100,'p1','sc1',:r,'KeepMe')"), {"r": rad})
        c.execute(text("INSERT INTO material_data(project_material_id,property_type_id,value) "
                       "VALUES (100,:cr,'200')"), {"cr": cr})
        c.execute(text("INSERT INTO scenario_object(id,scenario_id,project_id,name,object_type_id,helios_uuids) "
                       "VALUES (1,'sc1','p1','Ground',:g,'[]')"), {"g": grd})
        c.execute(text("INSERT INTO object_material(scenario_object_id,project_material_id,material_type_id,sync) "
                       "VALUES (1,100,:r,0)"), {"r": rad})
        c.execute(text("INSERT INTO object_property_data(scenario_object_id,project_material_id,property_type_id,value) "
                       "VALUES (1,100,:cr,'200')"), {"cr": cr})   # frozen snapshot
        c.execute(text("INSERT INTO object_property_data(scenario_object_id,project_material_id,property_type_id,value) "
                       "VALUES (1,NULL,:ln,'5')"), {"ln": ln})    # intrinsic

    database.run_migrations()   # applies 022 on the populated DB

    assert 22 in _versions(temp_engine)
    assert "material_group_id" in _columns(temp_engine, "project_material")
    assert "project_id" not in _columns(temp_engine, "project_material")
    assert "name" not in _columns(temp_engine, "project_material")
    assert "sync" not in _columns(temp_engine, "object_material")
    assert "material_group_id" in _columns(temp_engine, "object_material")

    with temp_engine.begin() as c:
        # Wrapped group: same id, name + provenance carried.
        grp = c.execute(text(
            "SELECT project_id, scenario_id, name FROM material_group WHERE id=100"
        )).fetchone()
        assert grp == ("p1", "sc1", "KeepMe")
        # The six mig-019 defaults are wrapped too (NULL-project global groups).
        assert c.execute(text(
            "SELECT count(*) FROM material_group WHERE project_id IS NULL"
        )).scalar() == 6
        # Member reshaped, values preserved.
        assert c.execute(text(
            "SELECT material_group_id FROM project_material WHERE id=100")).scalar() == 100
        assert c.execute(text(
            "SELECT value FROM material_data WHERE project_material_id=100 "
            "AND property_type_id=:cr"), {"cr": c.execute(text(
                "SELECT id FROM property_type WHERE property='color_r'")).scalar()}
        ).scalar() == "200"
        # Materialized row rebuilt with attribution; assignment seeded with sync.
        om = c.execute(text(
            "SELECT material_group_id FROM object_material "
            "WHERE scenario_object_id=1 AND project_material_id=100")).scalar()
        assert om == 100
        assert c.execute(text(
            "SELECT sync FROM object_material_group "
            "WHERE scenario_object_id=1 AND material_group_id=100")).scalar() == 0
        # Snapshot + intrinsic rows preserved.
        assert c.execute(text(
            "SELECT count(*) FROM object_property_data WHERE project_material_id=100"
        )).scalar() == 1
        assert c.execute(text(
            "SELECT count(*) FROM object_property_data WHERE project_material_id IS NULL"
        )).scalar() == 1
        # Whole-DB referential integrity after the rebuilds.
        assert c.execute(text("PRAGMA foreign_key_check")).fetchall() == []

    # The BREAK POINT: with FK enforcement ON, deleting the group cascades the
    # library side only — the applied rows and the frozen snapshot survive.
    raw = temp_engine.raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("DELETE FROM material_group WHERE id=100")
        raw.commit()
        cur.execute("SELECT count(*) FROM project_material WHERE id=100")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM material_data WHERE project_material_id=100")
        assert cur.fetchone()[0] == 0
        cur.execute("SELECT count(*) FROM object_material WHERE project_material_id=100")
        assert cur.fetchone()[0] == 1        # survives (soft reference)
        cur.execute("SELECT count(*) FROM object_material_group WHERE material_group_id=100")
        assert cur.fetchone()[0] == 1        # survives (soft reference)
        cur.execute("SELECT count(*) FROM object_property_data WHERE project_material_id=100")
        assert cur.fetchone()[0] == 1        # frozen snapshot survives
        # ...and deleting the materialized row still cascades its snapshot.
        cur.execute("DELETE FROM object_material WHERE scenario_object_id=1 AND project_material_id=100")
        raw.commit()
        cur.execute("SELECT count(*) FROM object_property_data WHERE project_material_id=100")
        assert cur.fetchone()[0] == 0
    finally:
        raw.close()


def test_clean_install_applies_every_migration(temp_engine):
    """A fresh DB runs the full contiguous sequence."""
    database.run_migrations()
    assert {9, 10, 14} <= _versions(temp_engine)
    assert "to_base_factor" in _columns(temp_engine, "data_units")


def test_rerun_tolerates_already_applied_column(temp_engine):
    """
    Reproduces the reported crash: the schema already has to_base_factor but
    version 9 is unrecorded (history drift). The old runner raised
    `duplicate column name: to_base_factor`; the new one treats it as a no-op
    and re-stamps the version.
    """
    database.run_migrations()  # build a full, current DB
    with temp_engine.begin() as conn:  # simulate the drift
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 9"))

    database.run_migrations()  # must NOT raise

    assert 9 in _versions(temp_engine)
    assert "to_base_factor" in _columns(temp_engine, "data_units")


def test_rerun_does_not_rebuild_projects_when_010_unrecorded(temp_engine):
    """
    Migration 010 rebuilds `projects` to convert utc_offset REAL -> TEXT and
    is NOT a safe no-op. If a drifted DB already stores TEXT offsets but never
    recorded version 10, the runner must detect the finished state and skip
    the rebuild — re-running it would mangle half-hour offsets.
    """
    database.run_migrations()
    with temp_engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO projects (id, session_id, name, utc_offset) "
            "VALUES ('p1', 's1', 'Proj', '+05:30')"
        ))
        conn.execute(text("DELETE FROM schema_migrations WHERE version = 10"))

    database.run_migrations()  # must skip the destructive rebuild

    with temp_engine.begin() as conn:
        offset = conn.execute(
            text("SELECT utc_offset FROM projects WHERE id = 'p1'")
        ).scalar()
    assert offset == "+05:30"          # offset preserved, not mangled to +05:00
    assert 10 in _versions(temp_engine)  # and stamped applied
