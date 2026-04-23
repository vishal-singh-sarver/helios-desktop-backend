"""
Tests for scenario CRUD + weather-scenario isolation.

Covers:
- Project creation auto-creates a "main" scenario
- Scenario create / list / delete
- Fork semantics (source_scenario_id copies weather CSV)
- Weather-scenario isolation (edits to one don't affect the other)
- Delete-project cascades to scenarios (DB + memory + disk)
- Auth: wrong session, unknown project/scenario
"""
from pathlib import Path
from uuid import uuid4

from app.core.config import settings
from app.core.session_store import registry
from app.db.database import SessionLocal
from app.db.models import Scenario


# ─────────────────────────── Helpers ─────────────────────────────────────────


CLEAN_CSV_A = (
    b"date,time,temperature,humidity\n"
    b"2023-07-13,10:00:00,22.5,65\n"
    b"2023-07-13,11:00:00,23.8,62\n"
)

CLEAN_CSV_B = (
    b"date,time,temperature,humidity\n"
    b"2023-08-01,09:00:00,18.0,80\n"
    b"2023-08-01,10:00:00,19.5,78\n"
)


def _make_project(client) -> tuple[str, str, str]:
    """Create a project and return (session_id, project_id, main_scenario_id)."""
    sid = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"Scenario_{uuid4().hex[:8]}",
        "latitude": 38.5,
        "longitude": -121.7,
    }
    r = client.post("/api/project/create", json=payload, headers={"session-id": sid})
    assert r.status_code == 201
    body = r.json()
    return sid, body["project_id"], body["main_scenario_id"]


def _scenario_dir(project_id: str, scenario_id: str) -> Path:
    return settings.data_dir / project_id / scenario_id


def _upload(client, session_id, project_id, scenario_id, csv_bytes):
    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id, "scenario-id": scenario_id},
        files={"file": ("t.csv", csv_bytes, "text/csv")},
    )
    assert r.status_code == 200


# ─────────────────────────── Auto-creation of "main" ─────────────────────────


def test_project_create_auto_creates_main_scenario(client):
    session_id, project_id, main_scenario_id = _make_project(client)

    # DB has the scenario row
    db = SessionLocal()
    try:
        scenario = db.query(Scenario).filter(Scenario.id == main_scenario_id).first()
        assert scenario is not None
        assert scenario.project_id == project_id
        assert scenario.name == "main"
        assert scenario.weather_file_path is None
        assert scenario.context_file_path is None
    finally:
        db.close()

    # Memory has the ScenarioContext ready to go
    sctx = registry.get_scenario_context(session_id, project_id, main_scenario_id)
    assert sctx is not None
    assert sctx.project_id == project_id
    assert sctx.scenario_id == main_scenario_id


# ─────────────────────────── Create scenario ─────────────────────────────────


def test_create_scenario_empty_start(client):
    session_id, project_id, _ = _make_project(client)

    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "baseline"},
        headers={"session-id": session_id},
    )

    assert r.status_code == 201
    body = r.json()
    assert body["success"] is True
    assert body["name"] == "baseline"
    assert "scenario_id" in body

    # The new scenario has no weather file yet
    new_sid = body["scenario_id"]
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter(Scenario.id == new_sid).first()
        assert row is not None
        assert row.weather_file_path is None
        assert row.context_file_path is None
    finally:
        db.close()


def test_create_scenario_forks_weather_from_source(client):
    session_id, project_id, main_sid = _make_project(client)
    _upload(client, session_id, project_id, main_sid, CLEAN_CSV_A)

    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "fork1", "source_scenario_id": main_sid},
        headers={"session-id": session_id},
    )
    assert r.status_code == 201
    new_sid = r.json()["scenario_id"]

    # New scenario's folder has a copied weather.csv — byte-for-byte
    # identical to the SOURCE's stored file (not the raw upload, which
    # gets transformed/re-serialized by the upload pipeline).
    src_csv = _scenario_dir(project_id, main_sid) / "weather.csv"
    new_csv = _scenario_dir(project_id, new_sid) / "weather.csv"
    assert new_csv.exists()
    assert new_csv.read_bytes() == src_csv.read_bytes()

    # DB row now has weather_file_path set
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter(Scenario.id == new_sid).first()
        assert row is not None
        assert row.weather_file_path is not None
        assert "weather.csv" in row.weather_file_path
    finally:
        db.close()


def test_create_scenario_duplicate_name_returns_400(client):
    session_id, project_id, _ = _make_project(client)

    r1 = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "dup"},
        headers={"session-id": session_id},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "dup"},
        headers={"session-id": session_id},
    )
    assert r2.status_code == 400
    assert "already exists" in r2.text.lower()


def test_create_scenario_empty_name_returns_422(client):
    session_id, project_id, _ = _make_project(client)
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "   "},
        headers={"session-id": session_id},
    )
    assert r.status_code == 422


def test_create_scenario_name_too_long_returns_422(client):
    session_id, project_id, _ = _make_project(client)
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "a" * 31},
        headers={"session-id": session_id},
    )
    assert r.status_code == 422


def test_create_scenario_wrong_session_returns_404(client):
    _, project_id, _ = _make_project(client)
    intruder = f"session_{uuid4().hex[:8]}"
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "other"},
        headers={"session-id": intruder},
    )
    assert r.status_code == 404


def test_create_scenario_unknown_source_returns_404(client):
    session_id, project_id, _ = _make_project(client)
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "fork", "source_scenario_id": uuid4().hex},
        headers={"session-id": session_id},
    )
    assert r.status_code == 404


# ─────────────────────────── List scenarios ──────────────────────────────────


def test_list_scenarios_returns_main_then_new(client):
    session_id, project_id, main_sid = _make_project(client)

    # Create an additional scenario
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "extra"},
        headers={"session-id": session_id},
    )
    assert r.status_code == 201
    new_sid = r.json()["scenario_id"]

    r = client.get(
        f"/api/project/{project_id}/scenarios",
        headers={"session-id": session_id},
    )
    assert r.status_code == 200
    scenarios = r.json()["scenarios"]

    ids = [s["id"] for s in scenarios]
    names = [s["name"] for s in scenarios]
    assert main_sid in ids
    assert new_sid in ids
    assert "main" in names
    assert "extra" in names
    for s in scenarios:
        assert "has_weather" in s
        assert "created_at" in s


def test_list_scenarios_wrong_session_returns_404(client):
    _, project_id, _ = _make_project(client)
    intruder = f"session_{uuid4().hex[:8]}"
    r = client.get(
        f"/api/project/{project_id}/scenarios",
        headers={"session-id": intruder},
    )
    assert r.status_code == 404


# ─────────────────────────── Delete scenario ─────────────────────────────────


def test_delete_scenario_wipes_row_memory_and_disk(client):
    session_id, project_id, main_sid = _make_project(client)
    _upload(client, session_id, project_id, main_sid, CLEAN_CSV_A)

    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "todelete", "source_scenario_id": main_sid},
        headers={"session-id": session_id},
    )
    new_sid = r.json()["scenario_id"]
    scn_dir = _scenario_dir(project_id, new_sid)
    assert scn_dir.exists()

    r = client.delete(
        f"/api/project/{project_id}/scenarios/{new_sid}",
        headers={"session-id": session_id},
    )
    assert r.status_code == 200

    # DB row gone
    db = SessionLocal()
    try:
        row = db.query(Scenario).filter(Scenario.id == new_sid).first()
        assert row is None
    finally:
        db.close()

    # Memory gone
    assert registry.get_scenario_context(session_id, project_id, new_sid) is None

    # Disk gone
    assert not scn_dir.exists()


def test_delete_unknown_scenario_returns_404(client):
    session_id, project_id, _ = _make_project(client)
    r = client.delete(
        f"/api/project/{project_id}/scenarios/{uuid4().hex}",
        headers={"session-id": session_id},
    )
    assert r.status_code == 404


# ─────────────────────────── Isolation ───────────────────────────────────────


def test_two_scenarios_have_isolated_weather_data(client):
    session_id, project_id, main_sid = _make_project(client)

    # Upload different CSVs into two scenarios
    _upload(client, session_id, project_id, main_sid, CLEAN_CSV_A)

    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "other"},
        headers={"session-id": session_id},
    )
    other_sid = r.json()["scenario_id"]
    _upload(client, session_id, project_id, other_sid, CLEAN_CSV_B)

    # Verify each scenario's CSV file on disk has its own data
    main_csv = _scenario_dir(project_id, main_sid) / "weather.csv"
    other_csv = _scenario_dir(project_id, other_sid) / "weather.csv"
    assert main_csv.exists()
    assert other_csv.exists()

    main_first_data_line = main_csv.read_text(encoding="utf-8").splitlines()[1]
    other_first_data_line = other_csv.read_text(encoding="utf-8").splitlines()[1]
    assert main_first_data_line.startswith("2023-07-13")   # main has CSV A
    assert other_first_data_line.startswith("2023-08-01")  # other has CSV B


# ─────────────────────────── Cascade delete ──────────────────────────────────


def test_delete_project_cascades_to_scenarios(client):
    session_id, project_id, main_sid = _make_project(client)
    _upload(client, session_id, project_id, main_sid, CLEAN_CSV_A)

    # Create one extra scenario too
    r = client.post(
        f"/api/project/{project_id}/scenarios/create",
        json={"name": "extra"},
        headers={"session-id": session_id},
    )
    extra_sid = r.json()["scenario_id"]

    # Delete the project
    r = client.delete(
        f"/api/project/{project_id}",
        headers={"session-id": session_id},
    )
    assert r.status_code == 200

    # DB rows gone (CASCADE)
    db = SessionLocal()
    try:
        rows = db.query(Scenario).filter(Scenario.project_id == project_id).all()
        assert rows == []
    finally:
        db.close()

    # In-memory scenarios gone
    assert registry.get_scenario_context(session_id, project_id, main_sid) is None
    assert registry.get_scenario_context(session_id, project_id, extra_sid) is None

    # Disk folder gone
    assert not (settings.data_dir / project_id).exists()


# ─────────────────────────── Weather endpoint auth with scenarios ────────────


def test_weather_without_scenario_id_header_returns_400(client):
    session_id, project_id, main_sid = _make_project(client)

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},  # missing scenario-id
        files={"file": ("t.csv", CLEAN_CSV_A, "text/csv")},
    )
    assert r.status_code == 400
    assert "scenario_id" in r.text.lower()


def test_weather_with_unknown_scenario_returns_404(client):
    session_id, project_id, _ = _make_project(client)
    bogus = uuid4().hex
    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id, "scenario-id": bogus},
        files={"file": ("t.csv", CLEAN_CSV_A, "text/csv")},
    )
    assert r.status_code == 404
    assert "scenario" in r.text.lower()


def test_weather_with_scenario_from_another_project_returns_404(client):
    """A scenario exists, but under a different project — should 404."""
    session_id_a, project_a, main_sid_a = _make_project(client)
    _, project_b, main_sid_b = _make_project(client)

    # Try to use project_b's scenario with project_a's URL path
    r = client.post(
        f"/api/weather/{project_a}/uploadfile",
        headers={"session-id": session_id_a, "scenario-id": main_sid_b},
        files={"file": ("t.csv", CLEAN_CSV_A, "text/csv")},
    )
    assert r.status_code == 404
