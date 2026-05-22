"""
Tests for /api/data-types — the helios_data_types catalog endpoints.

Catalog is global (no session_id needed). Each test uses unique
uuid-suffixed names so the shared test DB doesn't cause cross-test
collisions.
"""
import pytest
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


# ─── 'check' data type seeded as a units-less catalog entry ────────────────


def test_check_data_type_seeded_with_no_units(client):
    """Migration 011 seeds a 'check' entry in helios_data_types — a
    boolean / checkbox-style measurement that has no associated units.
    Frontend uses this when a column should render as a checkbox instead
    of a numeric input."""
    r = client.get("/api/data-types/")
    by_name = {t["data_type"]: t for t in r.json()["data_types"]}

    assert "check" in by_name, "'check' data type should be seeded by migration 011"
    assert by_name["check"]["units"] == [], "'check' should have an empty units list"


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


# ─── GET /api/data-types/ — returns types with units nested ─────────────────
#
# The list endpoint includes each type's data_units. Types with no units
# come back with `units: []`.


def _make_unit(client, data_type_id: int) -> int:
    r = client.post(
        "/api/data-units/",
        json={"unit": f"u_{uuid4().hex[:8]}", "data_type_id": data_type_id},
    )
    assert r.status_code == 201, r.text
    return r.json()["data_unit"]["id"]


def test_list_returns_types_and_their_units(client):
    dt1, _ = _make_data_type(client)
    dt2, _ = _make_data_type(client)
    u1a = _make_unit(client, dt1)
    u1b = _make_unit(client, dt1)
    u2a = _make_unit(client, dt2)

    r = client.get("/api/data-types/")
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


def test_list_includes_type_with_no_units(client):
    """A data_type that has zero units must come back with units: []."""
    dt, _ = _make_data_type(client)

    r = client.get("/api/data-types/")
    assert r.status_code == 200

    by_id = {t["id"]: t for t in r.json()["data_types"]}
    assert dt in by_id
    assert by_id[dt]["units"] == []


def test_list_unit_payload_includes_conversion_fields(client):
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

    r = client.get("/api/data-types/")
    by_id = {t["id"]: t for t in r.json()["data_types"]}
    unit = by_id[dt]["units"][0]
    assert unit["to_base_factor"] == 0.5
    assert unit["to_base_offset"] == -10.0
    assert unit["is_base"] is True


# ─── Default catalog seeded by migration 011 ────────────────────────────────
#
# The 9 weather parameters defined in the design doc with their canonical
# units and conversion factors. Each parent has exactly one base unit
# (enforced by the partial unique index from migration 009).


def _by_name(client) -> dict[str, dict]:
    """Return the catalog as a {data_type_name: row} map for assertions."""
    r = client.get("/api/data-types/")
    assert r.status_code == 200
    return {t["data_type"]: t for t in r.json()["data_types"]}


def test_default_data_types_seeded(client):
    """All 9 weather parameters from the design doc are present.
    Names match the doc's "Key used" column — snake_case for the seven
    that have a key, Title Case for the two radiation types where the
    doc leaves "Key used" empty."""
    by_name = _by_name(client)
    expected = {
        "Direct Normal Radiation",
        "Diffuse Horizontal Radiation",
        "air_temperature",
        "air_pressure",
        "air_humidity",
        "wind_speed",
        "turbidity",
        "beta_soil",
        "air_CO2",
    }
    assert expected.issubset(set(by_name.keys()))


def test_each_default_type_has_exactly_one_base_unit(client):
    """Partial unique index from migration 009 enforces this, but verify
    the seed data didn't somehow ship with zero or multiple bases."""
    by_name = _by_name(client)
    for type_name in (
        "Direct Normal Radiation", "Diffuse Horizontal Radiation",
        "air_temperature", "air_pressure", "air_humidity", "wind_speed",
        "turbidity", "beta_soil", "air_CO2",
    ):
        units = by_name[type_name]["units"]
        bases = [u for u in units if u["is_base"]]
        assert len(bases) == 1, f"{type_name}: expected 1 base unit, got {len(bases)}"


def test_air_temperature_conversion_factors(client):
    """C → K and F → K factors round-trip to the canonical Kelvin values."""
    units = {u["unit"]: u for u in _by_name(client)["air_temperature"]["units"]}

    # 0°C should equal 273.15 K
    c = units["C"]
    assert 0 * c["to_base_factor"] + c["to_base_offset"] == pytest.approx(273.15)

    # 32°F should equal 273.15 K
    f = units["F"]
    assert 32 * f["to_base_factor"] + f["to_base_offset"] == pytest.approx(273.15, rel=1e-4)


def test_air_pressure_conversion_factors(client):
    units = {u["unit"]: u for u in _by_name(client)["air_pressure"]["units"]}
    # 1 atm should equal 101325 Pa
    assert 1 * units["atm"]["to_base_factor"] + units["atm"]["to_base_offset"] == 101325.0
    # 1 bar should equal 100000 Pa
    assert 1 * units["bar"]["to_base_factor"] + units["bar"]["to_base_offset"] == 100000.0


def test_wind_speed_conversion_factors(client):
    units = {u["unit"]: u for u in _by_name(client)["wind_speed"]["units"]}
    # 3.6 km/h should equal 1 m/s
    assert 3.6 * units["km/h"]["to_base_factor"] == pytest.approx(1.0)


def test_co2_conversion_factors(client):
    units = {u["unit"]: u for u in _by_name(client)["air_CO2"]["units"]}
    # 1000 ppb = 1 ppm
    assert 1000 * units["ppb"]["to_base_factor"] == pytest.approx(1.0)


def test_default_units_min_max_set_on_base(client):
    """Every unit (base + secondary) carries a min/max range per the
    Weather Parameter Unit Conversion Reference doc (migration 012)."""
    by_name = _by_name(client)

    # air_temperature: K (base) 223–350, C (secondary) -50.15–76.85
    units = {u["unit"]: u for u in by_name["air_temperature"]["units"]}
    assert units["K"]["min"] == 223
    assert units["K"]["max"] == 350
    assert units["C"]["min"] == -50.15
    assert units["C"]["max"] == 76.85

    # air_humidity: 0-1 (base) 0–1
    units = {u["unit"]: u for u in by_name["air_humidity"]["units"]}
    assert units["0-1"]["min"] == 0
    assert units["0-1"]["max"] == 1
