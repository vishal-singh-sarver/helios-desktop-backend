"""
Tests for /api/data-types — the helios_data_types catalog endpoints.

Catalog is global (no session_id needed). Each test uses unique
uuid-suffixed names so the shared test DB doesn't cause cross-test
collisions.
"""
from uuid import uuid4


# ─── Helper ──────────────────────────────────────────────────────────────────


def _make_data_type(client, *, description: str | None = None) -> tuple[int, str]:
    """Create a fresh data_type with a unique name. Returns (id, name)."""
    name = f"T_{uuid4().hex[:8]}"
    body: dict = {"data_type": name}
    if description is not None:
        body["description"] = description
    r = client.post("/api/data-types/", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data_type"]["id"], name


# ─── Create ──────────────────────────────────────────────────────────────────


def test_create_returns_201_with_full_row(client):
    name = f"Temperature_{uuid4().hex[:8]}"
    r = client.post(
        "/api/data-types/",
        json={"data_type": name, "description": "how hot or cold"},
    )
    assert r.status_code == 201

    body = r.json()
    assert body["success"] is True

    row = body["data_type"]
    assert row["data_type"] == name
    assert row["description"] == "how hot or cold"
    assert isinstance(row["id"], int)
    assert "created_at" in row
    assert "updated_at" in row


def test_duplicate_data_type_returns_409(client):
    _, name = _make_data_type(client)
    r = client.post("/api/data-types/", json={"data_type": name})
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_empty_data_type_returns_422(client):
    r = client.post("/api/data-types/", json={"data_type": ""})
    assert r.status_code == 422


def test_whitespace_only_data_type_returns_422(client):
    r = client.post("/api/data-types/", json={"data_type": "   "})
    assert r.status_code == 422


def test_oversized_data_type_returns_422(client):
    r = client.post("/api/data-types/", json={"data_type": "X" * 51})
    assert r.status_code == 422


# ─── List / Get ──────────────────────────────────────────────────────────────


def test_list_returns_ascending_id(client):
    id_a, _ = _make_data_type(client)
    id_b, _ = _make_data_type(client)

    r = client.get("/api/data-types/")
    assert r.status_code == 200
    ids = [d["id"] for d in r.json()["data_types"]]

    assert id_a in ids
    assert id_b in ids
    # `b` was created after `a` so it must come later in the ordered list
    assert ids.index(id_a) < ids.index(id_b)


def test_get_by_id_returns_full_row(client):
    id_, name = _make_data_type(client, description="hello")
    r = client.get(f"/api/data-types/{id_}")
    assert r.status_code == 200
    row = r.json()["data_type"]
    assert row["id"] == id_
    assert row["data_type"] == name
    assert row["description"] == "hello"


def test_get_unknown_id_returns_404(client):
    r = client.get("/api/data-types/99999999")
    assert r.status_code == 404


# ─── PATCH ───────────────────────────────────────────────────────────────────


def test_patch_renames(client):
    id_, _ = _make_data_type(client)
    new_name = f"Renamed_{uuid4().hex[:8]}"

    r = client.patch(f"/api/data-types/{id_}", json={"data_type": new_name})
    assert r.status_code == 200
    assert r.json()["data_type"]["data_type"] == new_name

    # Confirm GET sees the rename
    r = client.get(f"/api/data-types/{id_}")
    assert r.json()["data_type"]["data_type"] == new_name


def test_patch_partial_keeps_other_fields(client):
    id_, name = _make_data_type(client, description="orig")
    r = client.patch(f"/api/data-types/{id_}", json={"description": "updated"})
    assert r.status_code == 200
    row = r.json()["data_type"]
    assert row["data_type"] == name           # unchanged
    assert row["description"] == "updated"     # changed


def test_patch_rename_collision_returns_409(client):
    _, name_a = _make_data_type(client)
    id_b, _ = _make_data_type(client)

    r = client.patch(f"/api/data-types/{id_b}", json={"data_type": name_a})
    assert r.status_code == 409


def test_patch_unknown_id_returns_404(client):
    r = client.patch("/api/data-types/99999999", json={"data_type": "X"})
    assert r.status_code == 404


# ─── DELETE ──────────────────────────────────────────────────────────────────


def test_delete_returns_success_and_row_disappears(client):
    id_, _ = _make_data_type(client)

    r = client.delete(f"/api/data-types/{id_}")
    assert r.status_code == 200
    assert r.json() == {"success": True, "data_type_id": id_}

    # GET after delete → 404
    r = client.get(f"/api/data-types/{id_}")
    assert r.status_code == 404


def test_delete_unknown_id_returns_404(client):
    r = client.delete("/api/data-types/99999999")
    assert r.status_code == 404


# ─── /api/data-types/with-units ─────────────────────────────────────────────
#
# GET /with-units returns every data type with its data_units nested.
# Types with no units come back with `units: []`.


def _make_unit(client, data_type_id: int) -> int:
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": data_type_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["data_unit"]["id"]


def test_with_units_returns_types_and_their_units(client):
    dt1, _ = _make_data_type(client)
    dt2, _ = _make_data_type(client)
    u1a = _make_unit(client, dt1)
    u1b = _make_unit(client, dt1)
    u2a = _make_unit(client, dt2)

    r = client.get("/api/data-types/with-units")
    assert r.status_code == 200

    by_id = {t["id"]: t for t in r.json()["data_types"]}
    assert dt1 in by_id
    assert dt2 in by_id

    unit_ids_dt1 = [u["id"] for u in by_id[dt1]["units"]]
    unit_ids_dt2 = [u["id"] for u in by_id[dt2]["units"]]
    assert u1a in unit_ids_dt1
    assert u1b in unit_ids_dt1
    assert u2a in unit_ids_dt2
    assert u1a not in unit_ids_dt2  # units don't leak across types


def test_with_units_includes_type_with_no_units(client):
    """A data_type that has zero units must come back with units: []."""
    dt, _ = _make_data_type(client)

    r = client.get("/api/data-types/with-units")
    assert r.status_code == 200

    by_id = {t["id"]: t for t in r.json()["data_types"]}
    assert dt in by_id
    assert by_id[dt]["units"] == []


def test_with_units_unit_payload_includes_conversion_fields(client):
    """The nested unit objects must expose the conversion fields just like
    GET /api/data-units does."""
    dt, _ = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={
            "unit": f"u_{uuid4().hex[:8]}",
            "data_type_id": dt,
            "to_base_factor": 0.5,
            "to_base_offset": -10.0,
            "is_base": True,
        },
    )
    assert r.status_code == 201

    r = client.get("/api/data-types/with-units")
    by_id = {t["id"]: t for t in r.json()["data_types"]}
    unit = by_id[dt]["units"][0]
    assert unit["to_base_factor"] == 0.5
    assert unit["to_base_offset"] == -10.0
    assert unit["is_base"] is True


def test_with_units_path_does_not_collide_with_get_by_id(client):
    """`/with-units` must not be parsed as `/{data_type_id}` — that would
    return 404 (not an integer). Confirm the literal path wins."""
    r = client.get("/api/data-types/with-units")
    assert r.status_code == 200
    assert "data_types" in r.json()
