import pytest
from uuid import uuid4
from app.core.config import settings
from app.core.session_store import registry


def test_create_project_success(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"TestProject_{uuid4().hex[:8]}",
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 201
    data = r.json()

    assert data["success"] is True
    assert data["name"] == payload["name"]
    assert data["latitude"] == 28.6
    assert data["longitude"] == 77.2
    assert "project_id" in data
    assert "utc_offset" in data
    # utc_offset is now an ISO 8601 offset string ("+HH:MM" / "-HH:MM").
    # Migration 010 changed the column from REAL fractional hours to TEXT.
    import re
    assert isinstance(data["utc_offset"], str)
    assert re.fullmatch(r"[+-]\d{2}:\d{2}", data["utc_offset"]), data["utc_offset"]
    # New project auto-creates a "main" scenario
    assert "main_scenario_id" in data
    assert data["main_scenario_id"]

    # Project state must be initialized in memory under the correct session
    project_id = data["project_id"]
    pctx = registry.get_context(session_id, project_id)
    assert pctx is not None
    assert pctx.project_id == project_id
    assert pctx.initialized is True


def test_two_projects_same_user_have_separate_states(client):
    """Same user, two projects — each gets its own isolated ProjectContext."""
    session_id = f"session_{uuid4().hex[:8]}"

    r1 = client.post("/api/project/create", json={
        "name": f"ProjectA_{uuid4().hex[:8]}", "latitude": 10.0, "longitude": 20.0
    }, headers={"session-id": session_id})
    assert r1.status_code == 201

    r2 = client.post("/api/project/create", json={
        "name": f"ProjectB_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0
    }, headers={"session-id": session_id})
    assert r2.status_code == 201

    pid1 = r1.json()["project_id"]
    pid2 = r2.json()["project_id"]

    pctx1 = registry.get_context(session_id, pid1)
    pctx2 = registry.get_context(session_id, pid2)

    # Both exist
    assert pctx1 is not None
    assert pctx2 is not None

    # Each is a separate object
    assert pctx1 is not pctx2

    # Each knows its own project_id
    assert pctx1.project_id == pid1
    assert pctx2.project_id == pid2


def test_two_users_have_separate_sessions(client):
    """Two users — completely separate sessions, no cross-contamination."""
    session_a = f"session_{uuid4().hex[:8]}"
    session_b = f"session_{uuid4().hex[:8]}"

    r_a = client.post("/api/project/create", json={
        "name": f"UserAProject_{uuid4().hex[:8]}", "latitude": 10.0, "longitude": 20.0
    }, headers={"session-id": session_a})
    assert r_a.status_code == 201

    r_b = client.post("/api/project/create", json={
        "name": f"UserBProject_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0
    }, headers={"session-id": session_b})
    assert r_b.status_code == 201

    pid_a = r_a.json()["project_id"]
    pid_b = r_b.json()["project_id"]

    # User A's project not visible in User B's session and vice versa
    assert registry.get_context(session_a, pid_b) is None
    assert registry.get_context(session_b, pid_a) is None

    # Each user's project is in their own session
    assert registry.get_context(session_a, pid_a) is not None
    assert registry.get_context(session_b, pid_b) is not None


def test_missing_session_id_header_returns_400(client):
    """Request without session_id header must be rejected."""
    payload = {"name": f"NoSession_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2}
    r = client.post("/api/project/create", json=payload)
    assert r.status_code == 400
    assert "session_id" in r.text.lower()


def test_project_name_required(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": "   ",
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 422
    assert "Project name is required" in str(r.json())


def test_project_name_max_length(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": "a" * 31,
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 422
    assert "This field supports up to 30 characters only" in str(r.json())


def test_duplicate_project_case_insensitive(client):
    session_id = f"session_{uuid4().hex[:8]}"
    unique_name = f"DuplicateTest_{uuid4().hex[:8]}"

    payload = {
        "name": unique_name,
        "latitude": 28.6,
        "longitude": 77.2
    }

    r1 = client.post("/api/project/create", json=payload, headers={"session-id": session_id})
    assert r1.status_code == 201

    payload2 = {
        "name": unique_name.lower(),
        "latitude": 28.6,
        "longitude": 77.2
    }

    r2 = client.post("/api/project/create", json=payload2, headers={"session-id": session_id})

    assert r2.status_code == 409
    assert "already exists" in r2.text


def test_latitude_out_of_range(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"LatTest_{uuid4().hex[:8]}",
        "latitude": 100,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_longitude_out_of_range(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"LngTest_{uuid4().hex[:8]}",
        "latitude": 28.6,
        "longitude": 200
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_invalid_numeric_input(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"InvalidInput_{uuid4().hex[:8]}",
        "latitude": "abc",
        "longitude": "5:30"
    }

    r = client.post("/api/project/create", json=payload, headers={"session-id": session_id})

    assert r.status_code == 422
    assert "Invalid input" in str(r.json())


def test_recent_projects_are_scoped_and_sorted_with_size(client):
    session_a = f"session_{uuid4().hex[:8]}"
    session_b = f"session_{uuid4().hex[:8]}"

    # Create two projects in session A
    p1_payload = {"name": f"RecentA1_{uuid4().hex[:8]}", "latitude": 10.0, "longitude": 20.0}
    p2_payload = {"name": f"RecentA2_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0}
    p1 = client.post("/api/project/create", json=p1_payload, headers={"session-id": session_a})
    p2 = client.post("/api/project/create", json=p2_payload, headers={"session-id": session_a})
    assert p1.status_code == 201
    assert p2.status_code == 201

    # Create one project in session B (must not appear in session A list)
    other_payload = {"name": f"RecentB_{uuid4().hex[:8]}", "latitude": 12.0, "longitude": 22.0}
    other = client.post("/api/project/create", json=other_payload, headers={"session-id": session_b})
    assert other.status_code == 201

    p2_id = p2.json()["project_id"]
    # Project size is the total on-disk footprint of the project's nested
    # folder (scenario context XMLs, weather CSVs, archives) — not the old
    # project-level current.xml.gz, which was removed. Write a couple of
    # files in the nested layout to exercise the recursive sum.
    ctx_dir = settings.scenario_context_file_dir(p2_id, "s1")
    ctx_dir.mkdir(parents=True, exist_ok=True)
    weather_dir = settings.scenario_dir(p2_id, "s1") / "weather"
    weather_dir.mkdir(parents=True, exist_ok=True)
    file_a = ctx_dir / "context.xml"
    file_b = weather_dir / "weather.csv"
    file_a.write_bytes(b"<helios>data</helios>")
    file_b.write_bytes(b"date,time,temp\n2026-01-01,00:00:00,20\n")
    expected_size = file_a.stat().st_size + file_b.stat().st_size

    recent = client.get("/api/project/recent", headers={"session-id": session_a})
    assert recent.status_code == 200
    projects = recent.json()["projects"]

    assert len(projects) == 2
    assert projects[0]["id"] == p2_id  # updated_at DESC, second created should come first
    assert projects[0]["size"] == expected_size  # recursive sum of both files
    # The other project in session A has nothing persisted → size 0
    assert projects[1]["size"] == 0

    names = {item["name"] for item in projects}
    assert p1_payload["name"] in names
    assert p2_payload["name"] in names
    assert other_payload["name"] not in names


def test_delete_project_success_and_wrong_session_rejected(client):
    owner_session = f"session_{uuid4().hex[:8]}"
    wrong_session = f"session_{uuid4().hex[:8]}"

    payload = {"name": f"DeleteMe_{uuid4().hex[:8]}", "latitude": 15.0, "longitude": 30.0}
    created = client.post("/api/project/create", json=payload, headers={"session-id": owner_session})
    assert created.status_code == 201
    project_id = created.json()["project_id"]

    # Project state must exist in memory after creation
    pctx = registry.get_context(owner_session, project_id)
    assert pctx is not None

    # Wrong session cannot delete this project
    denied = client.delete(f"/api/project/{project_id}", headers={"session-id": wrong_session})
    assert denied.status_code == 404

    # Owner session can delete
    deleted = client.delete(f"/api/project/{project_id}", headers={"session-id": owner_session})
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    # In-memory project state must be removed after delete
    assert registry.get_context(owner_session, project_id) is None

    # After delete, it should disappear from recent list for owner
    recent_after = client.get("/api/project/recent", headers={"session-id": owner_session})
    assert recent_after.status_code == 200
    remaining_ids = {item["id"] for item in recent_after.json()["projects"]}
    assert project_id not in remaining_ids


# ─── GET /api/project/{project_id} — project + scenarios + headers ──────────


def _make_project(client, session_id: str | None = None) -> tuple[str, str, str]:
    """Returns (session_id, project_id, main_scenario_id)."""
    sid = session_id or f"session_{uuid4().hex[:8]}"
    r = client.post(
        "/api/project/create",
        json={"name": f"Pgw_{uuid4().hex[:8]}", "latitude": 10.0, "longitude": 20.0},
        headers={"session-id": sid},
    )
    assert r.status_code == 201
    body = r.json()
    return sid, body["project_id"], body["main_scenario_id"]


def _make_data_type(client) -> int:
    r = client.post("/api/data-types/", json={"data_type": f"T_{uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()["data_type"]["id"]


def _make_data_unit(client, data_type_id: int) -> int:
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": data_type_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["data_unit"]["id"]


def test_get_project_returns_project_and_main_scenario(client):
    sid, pid, scn = _make_project(client)

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    assert r.status_code == 200
    body = r.json()["project"]

    assert body["id"] == pid
    assert "name" in body
    assert "latitude" in body
    assert "longitude" in body
    assert "utc_offset" in body
    assert isinstance(body["scenarios"], list)
    assert len(body["scenarios"]) == 1

    main = body["scenarios"][0]
    assert main["id"] == scn
    assert main["name"] == "main"
    assert main["weather_data_headers"] == []


def test_get_project_includes_all_scenarios(client):
    sid, pid, _ = _make_project(client)

    # Add a second scenario
    r = client.post(
        f"/api/project/{pid}/scenarios/create",
        json={"name": "scenario_two"},
        headers={"session-id": sid},
    )
    assert r.status_code == 201

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    scenarios = r.json()["project"]["scenarios"]
    names = [s["name"] for s in scenarios]
    assert "main" in names
    assert "scenario_two" in names


def test_get_project_nests_weather_headers_per_scenario(client):
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Install a header on the main scenario
    r = client.put(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers={"session-id": sid},
        json={"headers": [
            {"name": "temp", "helios_data_type_id": dt, "unit_id": u, "display_order": 0}
        ]},
    )
    assert r.status_code == 200

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    main = r.json()["project"]["scenarios"][0]
    assert len(main["weather_data_headers"]) == 1
    h = main["weather_data_headers"][0]
    assert h["name"] == "temp"
    assert h["helios_data_type_id"] == dt
    assert h["unit_id"] == u


def test_get_project_headers_isolated_per_scenario(client):
    """Headers in scenario A must not appear under scenario B."""
    sid, pid, scn_a = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Create a second scenario
    r = client.post(
        f"/api/project/{pid}/scenarios/create",
        json={"name": "second"},
        headers={"session-id": sid},
    )
    scn_b = r.json()["scenario_id"]

    # Headers ONLY on scenario A
    client.put(
        f"/api/weather/project/{pid}/scenario/{scn_a}/weather_data_header",
        headers={"session-id": sid},
        json={"headers": [
            {"name": "only_in_a", "helios_data_type_id": dt, "unit_id": u, "display_order": 0}
        ]},
    )

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    by_id = {s["id"]: s for s in r.json()["project"]["scenarios"]}
    assert len(by_id[scn_a]["weather_data_headers"]) == 1
    assert by_id[scn_b]["weather_data_headers"] == []


def test_get_project_unknown_returns_404(client):
    sid = f"session_{uuid4().hex[:8]}"
    r = client.get(f"/api/project/{uuid4().hex}", headers={"session-id": sid})
    assert r.status_code == 404


def test_get_project_from_another_session_returns_404(client):
    _, pid, _ = _make_project(client)
    sid_intruder = f"session_{uuid4().hex[:8]}"

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid_intruder})
    assert r.status_code == 404


def test_get_project_does_not_collide_with_recent_route(client):
    """`/recent` is a literal path; it must not be parsed as a project_id."""
    sid = f"session_{uuid4().hex[:8]}"
    r = client.get("/api/project/recent", headers={"session-id": sid})
    assert r.status_code == 200
    assert "projects" in r.json()



# ─── PATCH /api/project/{project_id} ────────────────────────────────────────


def _make_simple_project(client, session_id: str | None = None) -> tuple[str, str]:
    """Create one project; return (session_id, project_id)."""
    sid = session_id or f"session_{uuid4().hex[:8]}"
    r = client.post(
        "/api/project/create",
        json={"name": f"P_{uuid4().hex[:8]}", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid},
    )
    assert r.status_code == 201, r.text
    return sid, r.json()["project_id"]


def test_patch_name_only_succeeds(client):
    sid, pid = _make_simple_project(client)
    new_name = f"Renamed_{uuid4().hex[:8]}"

    r = client.patch(f"/api/project/{pid}", json={"name": new_name},
                     headers={"session-id": sid})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["project"]["name"] == new_name
    assert body["project"]["latitude"] == 38.5  # unchanged

    # Confirm via GET
    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    assert r.json()["project"]["name"] == new_name


def test_patch_coords_recomputes_utc_offset(client):
    """Move from California (-121.7, UTC-8/-7) to Delhi (77.2, UTC+5:30).
    utc_offset must change."""
    sid, pid = _make_simple_project(client)

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    original_offset = r.json()["project"]["utc_offset"]

    r = client.patch(
        f"/api/project/{pid}",
        json={"latitude": 28.6, "longitude": 77.2},
        headers={"session-id": sid},
    )
    assert r.status_code == 200
    new_offset = r.json()["project"]["utc_offset"]
    assert new_offset != original_offset
    # Delhi is UTC+5:30
    assert new_offset.startswith("+05:30") or new_offset == "+05:30"


def test_patch_all_fields_at_once(client):
    sid, pid = _make_simple_project(client)
    new_name = f"All_{uuid4().hex[:8]}"

    r = client.patch(
        f"/api/project/{pid}",
        json={"name": new_name, "latitude": 51.5, "longitude": -0.12},
        headers={"session-id": sid},
    )
    assert r.status_code == 200
    p = r.json()["project"]
    assert p["name"] == new_name
    assert p["latitude"] == 51.5
    assert p["longitude"] == -0.12


def test_patch_empty_body_is_noop(client):
    sid, pid = _make_simple_project(client)

    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    before = r.json()["project"]

    r = client.patch(f"/api/project/{pid}", json={}, headers={"session-id": sid})
    assert r.status_code == 200
    after = r.json()["project"]
    assert after["name"] == before["name"]
    assert after["latitude"] == before["latitude"]
    assert after["longitude"] == before["longitude"]
    assert after["utc_offset"] == before["utc_offset"]


def test_patch_rename_to_self_is_not_409(client):
    """PATCH name to the project's current name → 200 no-op, not collision."""
    sid, pid = _make_simple_project(client)
    r = client.get(f"/api/project/{pid}", headers={"session-id": sid})
    current_name = r.json()["project"]["name"]

    r = client.patch(f"/api/project/{pid}", json={"name": current_name},
                     headers={"session-id": sid})
    assert r.status_code == 200
    assert r.json()["project"]["name"] == current_name


def test_patch_name_collision_in_session_returns_409(client):
    """Two projects in the same session; renaming one to the other's name → 409."""
    sid = f"session_{uuid4().hex[:8]}"
    r1 = client.post(
        "/api/project/create",
        json={"name": "FirstProj", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid},
    )
    r2 = client.post(
        "/api/project/create",
        json={"name": "SecondProj", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid},
    )
    second_pid = r2.json()["project_id"]

    r = client.patch(
        f"/api/project/{second_pid}",
        json={"name": "FirstProj"},
        headers={"session-id": sid},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_patch_same_name_in_different_session_succeeds(client):
    """Project name uniqueness is per-session — two sessions can have the
    same name without collision."""
    sid_a = f"session_{uuid4().hex[:8]}"
    sid_b = f"session_{uuid4().hex[:8]}"

    client.post(
        "/api/project/create",
        json={"name": "SharedName", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid_a},
    )
    rb = client.post(
        "/api/project/create",
        json={"name": "OtherProj", "latitude": 38.5, "longitude": -121.7},
        headers={"session-id": sid_b},
    )
    pid_b = rb.json()["project_id"]

    r = client.patch(
        f"/api/project/{pid_b}",
        json={"name": "SharedName"},
        headers={"session-id": sid_b},
    )
    assert r.status_code == 200
    assert r.json()["project"]["name"] == "SharedName"


def test_patch_invalid_lat_returns_422(client):
    sid, pid = _make_simple_project(client)
    r = client.patch(f"/api/project/{pid}", json={"latitude": 91.0},
                     headers={"session-id": sid})
    assert r.status_code == 422


def test_patch_invalid_long_returns_422(client):
    sid, pid = _make_simple_project(client)
    r = client.patch(f"/api/project/{pid}", json={"longitude": -181.0},
                     headers={"session-id": sid})
    assert r.status_code == 422


def test_patch_oversize_name_returns_422(client):
    sid, pid = _make_simple_project(client)
    r = client.patch(f"/api/project/{pid}", json={"name": "X" * 31},
                     headers={"session-id": sid})
    assert r.status_code == 422


def test_patch_empty_name_returns_422(client):
    sid, pid = _make_simple_project(client)
    r = client.patch(f"/api/project/{pid}", json={"name": "   "},
                     headers={"session-id": sid})
    assert r.status_code == 422


def test_patch_string_coords_are_coerced(client):
    """Mirror create's behavior: numeric strings are accepted and coerced."""
    sid, pid = _make_simple_project(client)
    r = client.patch(
        f"/api/project/{pid}",
        json={"latitude": "40.0", "longitude": "-74.0"},
        headers={"session-id": sid},
    )
    assert r.status_code == 200
    assert r.json()["project"]["latitude"] == 40.0
    assert r.json()["project"]["longitude"] == -74.0


def test_patch_unknown_project_returns_404(client):
    sid = f"session_{uuid4().hex[:8]}"
    r = client.patch(
        f"/api/project/{uuid4().hex}",
        json={"name": "X"},
        headers={"session-id": sid},
    )
    assert r.status_code == 404


def test_patch_other_session_project_returns_404(client):
    """Project owned by session A → session B PATCHing it sees 404."""
    sid_a, pid = _make_simple_project(client)
    sid_b = f"session_{uuid4().hex[:8]}"

    r = client.patch(
        f"/api/project/{pid}",
        json={"name": "hijacked"},
        headers={"session-id": sid_b},
    )
    assert r.status_code == 404


def test_patch_missing_session_id_returns_400(client):
    sid, pid = _make_simple_project(client)
    r = client.patch(f"/api/project/{pid}", json={"name": "X"})
    assert r.status_code == 400
