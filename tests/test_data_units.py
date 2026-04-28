"""
Tests for /api/data-units — the data_units catalog endpoints.

Each unit belongs to one helios_data_type via data_type_id (CASCADE on parent
delete). Catalog is global (no session_id).
"""
from uuid import uuid4


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _make_data_type(client) -> int:
    """Create a parent data_type. Returns its id."""
    r = client.post("/api/data-types/", json={"data_type": f"T_{uuid4().hex[:8]}"})
    assert r.status_code == 201, r.text
    return r.json()["data_type"]["id"]


def _make_data_unit(
    client,
    data_type_id: int,
    *,
    unit: str | None = None,
    alias: str | None = None,
    min: float | None = None,
    max: float | None = None,
) -> tuple[int, str]:
    """Create a unit under the given parent. Returns (id, unit_name)."""
    body: dict = {
        "unit": unit or f"u_{uuid4().hex[:8]}",
        "data_type_id": data_type_id,
    }
    if alias is not None:
        body["alias"] = alias
    if min is not None:
        body["min"] = min
    if max is not None:
        body["max"] = max
    r = client.post("/api/data-units/", json=body)
    assert r.status_code == 201, r.text
    return r.json()["data_unit"]["id"], body["unit"]


# ─── Create ──────────────────────────────────────────────────────────────────


def test_create_with_min_max_returns_201(client):
    dt_id = _make_data_type(client)
    unit_name = f"u_{uuid4().hex[:8]}"

    r = client.post(
        "/api/data-units/",
        json={
            "unit": unit_name,
            "alias": "U",
            "data_type_id": dt_id,
            "min": -40,
            "max": 60,
        },
    )
    assert r.status_code == 201

    body = r.json()
    assert body["success"] is True

    row = body["data_unit"]
    assert row["unit"] == unit_name
    assert row["alias"] == "U"
    assert row["data_type_id"] == dt_id
    assert row["min"] == -40
    assert row["max"] == 60
    assert isinstance(row["id"], int)


def test_unknown_data_type_id_returns_404(client):
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": 99999999},
    )
    assert r.status_code == 404


def test_duplicate_unit_under_same_type_returns_409(client):
    dt_id = _make_data_type(client)
    unit_name = f"u_{uuid4().hex[:8]}"

    r1 = client.post("/api/data-units/", json={"unit": unit_name, "data_type_id": dt_id})
    assert r1.status_code == 201

    r2 = client.post("/api/data-units/", json={"unit": unit_name, "data_type_id": dt_id})
    assert r2.status_code == 409
    assert "already exists" in r2.json()["detail"].lower()


def test_same_unit_name_under_different_types_succeeds(client):
    """The unique constraint is (data_type_id, unit) — not unit alone.
    Same unit name under two different parent types should both succeed."""
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)
    unit_name = f"u_{uuid4().hex[:8]}"

    r1 = client.post("/api/data-units/", json={"unit": unit_name, "data_type_id": dt1})
    r2 = client.post("/api/data-units/", json={"unit": unit_name, "data_type_id": dt2})

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["data_unit"]["id"] != r2.json()["data_unit"]["id"]


def test_create_min_greater_than_max_returns_422(client):
    dt_id = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={
            "unit": f"u_{uuid4().hex[:8]}",
            "data_type_id": dt_id,
            "min": 100,
            "max": 50,
        },
    )
    assert r.status_code == 422


# ─── List / Get ──────────────────────────────────────────────────────────────


def test_list_filtered_by_data_type_id(client):
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)

    u1_id, _ = _make_data_unit(client, dt1)
    u2_id, _ = _make_data_unit(client, dt2)

    r = client.get(f"/api/data-units/?data_type_id={dt1}")
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()["data_units"]]
    assert u1_id in ids
    assert u2_id not in ids

    # Also verify all returned rows have the matching data_type_id
    for u in r.json()["data_units"]:
        assert u["data_type_id"] == dt1


def test_list_unfiltered_returns_all(client):
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)
    u1_id, _ = _make_data_unit(client, dt1)
    u2_id, _ = _make_data_unit(client, dt2)

    r = client.get("/api/data-units/")
    assert r.status_code == 200
    ids = [u["id"] for u in r.json()["data_units"]]
    assert u1_id in ids
    assert u2_id in ids


def test_get_by_id_returns_full_row(client):
    dt_id = _make_data_type(client)
    unit_id, unit_name = _make_data_unit(client, dt_id, alias="U", min=0, max=100)

    r = client.get(f"/api/data-units/{unit_id}")
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["id"] == unit_id
    assert row["unit"] == unit_name
    assert row["alias"] == "U"
    assert row["min"] == 0
    assert row["max"] == 100


def test_get_unknown_unit_returns_404(client):
    r = client.get("/api/data-units/99999999")
    assert r.status_code == 404


# ─── PATCH ───────────────────────────────────────────────────────────────────


def test_patch_min_only(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id, min=0, max=100)

    r = client.patch(f"/api/data-units/{unit_id}", json={"min": 10})
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["min"] == 10
    assert row["max"] == 100   # unchanged


def test_patch_max_only(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id, min=0, max=100)

    r = client.patch(f"/api/data-units/{unit_id}", json={"max": 50})
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["min"] == 0     # unchanged
    assert row["max"] == 50


def test_patch_both_min_and_max(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id, min=0, max=100)

    r = client.patch(f"/api/data-units/{unit_id}", json={"min": 5, "max": 95})
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["min"] == 5
    assert row["max"] == 95


def test_patch_both_null_is_no_op(client):
    """Sending null for both means 'don't change' — service ignores None.
    (Distinguishing 'clear to null' from 'don't change' would need exclude_unset
    semantics, which is out of scope per the doc.)"""
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id, min=0, max=100)

    r = client.patch(f"/api/data-units/{unit_id}", json={"min": None, "max": None})
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["min"] == 0       # unchanged
    assert row["max"] == 100     # unchanged


def test_patch_ignores_data_type_id(client):
    """data_type_id is immutable. The schema doesn't expose the field, so any
    value the client sends should be silently dropped by Pydantic."""
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt1)

    # Try to "move" the unit from dt1 to dt2
    r = client.patch(f"/api/data-units/{unit_id}", json={"data_type_id": dt2})
    assert r.status_code == 200

    # Parent did NOT change
    assert r.json()["data_unit"]["data_type_id"] == dt1


def test_patch_min_greater_than_max_returns_422(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id, min=0, max=100)

    r = client.patch(f"/api/data-units/{unit_id}", json={"min": 100, "max": 50})
    assert r.status_code == 422


def test_patch_unknown_unit_returns_404(client):
    r = client.patch("/api/data-units/99999999", json={"min": 0})
    assert r.status_code == 404


# ─── DELETE ──────────────────────────────────────────────────────────────────


def test_delete_returns_success_and_row_disappears(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)

    r = client.delete(f"/api/data-units/{unit_id}")
    assert r.status_code == 200
    assert r.json() == {"success": True, "data_unit_id": unit_id}

    r = client.get(f"/api/data-units/{unit_id}")
    assert r.status_code == 404


def test_delete_unknown_unit_returns_404(client):
    r = client.delete("/api/data-units/99999999")
    assert r.status_code == 404


def test_cascade_delete_parent_removes_unit(client):
    """Deleting the parent data_type cascades to its data_units."""
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)

    # Sanity: unit exists
    r = client.get(f"/api/data-units/{unit_id}")
    assert r.status_code == 200

    # Delete parent
    r = client.delete(f"/api/data-types/{dt_id}")
    assert r.status_code == 200

    # Unit is gone via CASCADE
    r = client.get(f"/api/data-units/{unit_id}")
    assert r.status_code == 404


# ─── Conversion fields (factor / offset / is_base) ──────────────────────────
#
# Migration 010 adds three columns describing the affine map back to the
# data_type's canonical unit:
#     value_in_base = value * to_base_factor + to_base_offset
# A partial unique index enforces at most one is_base=1 row per data_type.


def test_create_default_conversion_values(client):
    """If the client doesn't send conversion fields, defaults are
    factor=1.0, offset=0.0, is_base=False (the no-op transform)."""
    dt_id = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt_id},
    )
    assert r.status_code == 201
    row = r.json()["data_unit"]
    assert row["to_base_factor"] == 1.0
    assert row["to_base_offset"] == 0.0
    assert row["is_base"] is False


def test_create_with_explicit_conversion_values(client):
    """Round-trip: sent values come back from GET unchanged."""
    dt_id = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={
            "unit": f"u_{uuid4().hex[:8]}",
            "data_type_id": dt_id,
            "to_base_factor": 5.0 / 9.0,
            "to_base_offset": -160.0 / 9.0,
            "is_base": False,
        },
    )
    assert r.status_code == 201
    unit_id = r.json()["data_unit"]["id"]

    r = client.get(f"/api/data-units/{unit_id}")
    row = r.json()["data_unit"]
    assert row["to_base_factor"] == 5.0 / 9.0
    assert row["to_base_offset"] == -160.0 / 9.0
    assert row["is_base"] is False


def test_create_first_base_unit_succeeds(client):
    dt_id = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt_id, "is_base": True},
    )
    assert r.status_code == 201
    assert r.json()["data_unit"]["is_base"] is True


def test_create_second_base_for_same_type_returns_409(client):
    """Partial unique index: at most one is_base=1 per data_type."""
    dt_id = _make_data_type(client)
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt_id, "is_base": True},
    )
    assert r.status_code == 201

    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt_id, "is_base": True},
    )
    assert r.status_code == 409
    assert "base" in r.json()["detail"].lower()


def test_two_bases_under_different_types_succeed(client):
    """The constraint scopes per data_type — different types can each have
    their own base."""
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)

    r1 = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt1, "is_base": True},
    )
    r2 = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": dt2, "is_base": True},
    )
    assert r1.status_code == 201
    assert r2.status_code == 201


def test_patch_conversion_factor_and_offset(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)

    r = client.patch(
        f"/api/data-units/{unit_id}",
        json={"to_base_factor": 2.5, "to_base_offset": 1.5},
    )
    assert r.status_code == 200
    row = r.json()["data_unit"]
    assert row["to_base_factor"] == 2.5
    assert row["to_base_offset"] == 1.5


def test_patch_promote_to_base_succeeds_when_no_existing_base(client):
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)

    r = client.patch(f"/api/data-units/{unit_id}", json={"is_base": True})
    assert r.status_code == 200
    assert r.json()["data_unit"]["is_base"] is True


def test_patch_promote_to_base_returns_409_when_other_unit_is_base(client):
    """Promoting a unit to base when another unit already holds the base flag
    for the same data_type must be rejected with a clear 409, not a leaky
    IntegrityError from the partial unique index."""
    dt_id = _make_data_type(client)
    base_id, _ = _make_data_unit(client, dt_id)
    other_id, _ = _make_data_unit(client, dt_id)

    # First unit becomes base
    r = client.patch(f"/api/data-units/{base_id}", json={"is_base": True})
    assert r.status_code == 200

    # Second unit can't also become base
    r = client.patch(f"/api/data-units/{other_id}", json={"is_base": True})
    assert r.status_code == 409
    assert "base" in r.json()["detail"].lower()


def test_patch_demote_from_base(client):
    """Setting is_base back to False on an existing base is allowed (no
    constraint blocks it). After demotion, the type has no base unit."""
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)

    client.patch(f"/api/data-units/{unit_id}", json={"is_base": True})

    r = client.patch(f"/api/data-units/{unit_id}", json={"is_base": False})
    assert r.status_code == 200
    assert r.json()["data_unit"]["is_base"] is False


def test_patch_is_base_idempotent_on_same_row(client):
    """PATCH is_base=True on the row that's ALREADY base — no other unit
    holds the flag, so the pre-check should let it through unchanged."""
    dt_id = _make_data_type(client)
    unit_id, _ = _make_data_unit(client, dt_id)
    client.patch(f"/api/data-units/{unit_id}", json={"is_base": True})

    r = client.patch(f"/api/data-units/{unit_id}", json={"is_base": True})
    assert r.status_code == 200
    assert r.json()["data_unit"]["is_base"] is True
