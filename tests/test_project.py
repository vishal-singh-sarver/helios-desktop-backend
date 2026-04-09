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

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

    assert r.status_code == 201
    data = r.json()

    assert data["success"] is True
    assert data["name"] == payload["name"]
    assert data["latitude"] == 28.6
    assert data["longitude"] == 77.2
    assert "project_id" in data
    assert "utc_offset" in data

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
    }, headers={"session_id": session_id})
    assert r1.status_code == 201

    r2 = client.post("/api/project/create", json={
        "name": f"ProjectB_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0
    }, headers={"session_id": session_id})
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
    }, headers={"session_id": session_a})
    assert r_a.status_code == 201

    r_b = client.post("/api/project/create", json={
        "name": f"UserBProject_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0
    }, headers={"session_id": session_b})
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

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

    assert r.status_code == 422
    assert "Project name is required" in str(r.json())


def test_project_name_max_length(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": "a" * 31,
        "latitude": 28.6,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

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

    r1 = client.post("/api/project/create", json=payload, headers={"session_id": session_id})
    assert r1.status_code == 201

    payload2 = {
        "name": unique_name.lower(),
        "latitude": 28.6,
        "longitude": 77.2
    }

    r2 = client.post("/api/project/create", json=payload2, headers={"session_id": session_id})

    assert r2.status_code == 409
    assert "already exists" in r2.text


def test_latitude_out_of_range(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"LatTest_{uuid4().hex[:8]}",
        "latitude": 100,
        "longitude": 77.2
    }

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_longitude_out_of_range(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"LngTest_{uuid4().hex[:8]}",
        "latitude": 28.6,
        "longitude": 200
    }

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

    assert r.status_code == 422
    assert "Invalid" in str(r.json())


def test_invalid_numeric_input(client):
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"InvalidInput_{uuid4().hex[:8]}",
        "latitude": "abc",
        "longitude": "5:30"
    }

    r = client.post("/api/project/create", json=payload, headers={"session_id": session_id})

    assert r.status_code == 422
    assert "Invalid input" in str(r.json())


def test_recent_projects_are_scoped_and_sorted_with_size(client):
    session_a = f"session_{uuid4().hex[:8]}"
    session_b = f"session_{uuid4().hex[:8]}"

    # Create two projects in session A
    p1_payload = {"name": f"RecentA1_{uuid4().hex[:8]}", "latitude": 10.0, "longitude": 20.0}
    p2_payload = {"name": f"RecentA2_{uuid4().hex[:8]}", "latitude": 11.0, "longitude": 21.0}
    p1 = client.post("/api/project/create", json=p1_payload, headers={"session_id": session_a})
    p2 = client.post("/api/project/create", json=p2_payload, headers={"session_id": session_a})
    assert p1.status_code == 201
    assert p2.status_code == 201

    # Create one project in session B (must not appear in session A list)
    other_payload = {"name": f"RecentB_{uuid4().hex[:8]}", "latitude": 12.0, "longitude": 22.0}
    other = client.post("/api/project/create", json=other_payload, headers={"session_id": session_b})
    assert other.status_code == 201

    p2_id = p2.json()["project_id"]
    snapshot_path = settings.resolved_projects_dir / p2_id / "current.xml.gz"
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    snapshot_bytes = b"sample_snapshot_payload"
    snapshot_path.write_bytes(snapshot_bytes)

    recent = client.get("/api/project/recent", headers={"session_id": session_a})
    assert recent.status_code == 200
    projects = recent.json()["projects"]

    assert len(projects) == 2
    assert projects[0]["id"] == p2_id  # updated_at DESC, second created should come first
    assert projects[0]["size"] == len(snapshot_bytes)

    names = {item["name"] for item in projects}
    assert p1_payload["name"] in names
    assert p2_payload["name"] in names
    assert other_payload["name"] not in names


def test_delete_project_success_and_wrong_session_rejected(client):
    owner_session = f"session_{uuid4().hex[:8]}"
    wrong_session = f"session_{uuid4().hex[:8]}"

    payload = {"name": f"DeleteMe_{uuid4().hex[:8]}", "latitude": 15.0, "longitude": 30.0}
    created = client.post("/api/project/create", json=payload, headers={"session_id": owner_session})
    assert created.status_code == 201
    project_id = created.json()["project_id"]

    # Project state must exist in memory after creation
    pctx = registry.get_context(owner_session, project_id)
    assert pctx is not None

    # Wrong session cannot delete this project
    denied = client.delete(f"/api/project/{project_id}", headers={"session_id": wrong_session})
    assert denied.status_code == 404

    # Owner session can delete
    deleted = client.delete(f"/api/project/{project_id}", headers={"session_id": owner_session})
    assert deleted.status_code == 200
    assert deleted.json()["success"] is True

    # In-memory project state must be removed after delete
    assert registry.get_context(owner_session, project_id) is None

    # After delete, it should disappear from recent list for owner
    recent_after = client.get("/api/project/recent", headers={"session_id": owner_session})
    assert recent_after.status_code == 200
    remaining_ids = {item["id"] for item in recent_after.json()["projects"]}
    assert project_id not in remaining_ids
