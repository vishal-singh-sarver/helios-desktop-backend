"""
Tests for the weather endpoints + the auto-transform layer.

Covers:
- Unit tests for _transform_csv (CIMIS, ISO 8601, semicolon, AM/PM, fallbacks, rejections)
- Integration tests via TestClient for the six weather endpoints
- Auth / scope tests (wrong session, missing project, missing headers)

URL shape (all endpoints):
    /api/weather/project/{project_id}/scenario/{scenario_id}/<verb>
"""
from uuid import uuid4

import pytest

from app.services.weather_service import _transform_csv


# ─────────────────────────── Helpers ─────────────────────────────────────────


def _make_project(client) -> tuple[str, str, str]:
    """Create a project. Returns (session_id, project_id, main_scenario_id).

    Every new project auto-creates a 'main' scenario; weather endpoints
    require a scenario in the URL path.
    """
    session_id = f"session_{uuid4().hex[:8]}"
    payload = {
        "name": f"Weather_{uuid4().hex[:8]}",
        "latitude": 38.5,
        "longitude": -121.7,
    }
    r = client.post(
        "/api/project/create",
        json=payload,
        headers={"session-id": session_id},
    )
    assert r.status_code == 201
    body = r.json()
    return session_id, body["project_id"], body["main_scenario_id"]


def _session_headers(session_id: str) -> dict:
    return {"session-id": session_id}


def _url(project_id: str, scenario_id: str, verb: str) -> str:
    return f"/api/weather/project/{project_id}/scenario/{scenario_id}/{verb}"


CLEAN_CSV = (
    b"date,time,temperature,humidity\n"
    b"2023-07-13,10:00:00,22.5,65\n"
    b"2023-07-13,11:00:00,23.8,62\n"
    b"2023-07-13,12:00:00,25.3,58\n"
)

CIMIS_CSV = (
    b"Stn Id,Stn Name,CIMIS Region,Date,Hour,ETo (mm),qc,Air Temp (C),qc,Rel Hum (%),qc\n"
    b"6,Davis,Sacramento Valley,7/13/2023,0100,0.00, ,16.0, ,76, \n"
    b"6,Davis,Sacramento Valley,7/13/2023,1400,0.71, ,32.0, ,42, \n"
    b"6,Davis,Sacramento Valley,7/13/2023,2400,0.02, ,19.5, ,66, \n"
)


# ─────────────────────────── Transformer unit tests ─────────────────────────


def test_transform_already_clean_format_is_idempotent():
    header, rows = _transform_csv(CLEAN_CSV)
    assert header == ["date", "time", "temperature", "humidity"]
    assert rows[0] == ["2023-07-13", "10:00:00", "22.5", "65"]
    assert len(rows) == 3


def test_transform_cimis_format():
    """Split date+HHMM, qc flag columns, Stn Name as text, '2400' rollover."""
    header, rows = _transform_csv(CIMIS_CSV)

    assert header == ["date", "time", "stn_id", "eto_mm", "air_temp_c", "rel_hum"]
    assert "stn_name" not in header
    assert "cimis_region" not in header
    assert "qc" not in header

    assert rows[0][:2] == ["2023-07-13", "01:00:00"]
    assert rows[1][:2] == ["2023-07-13", "14:00:00"]
    # 2400 rolls over to next day's 00:00:00
    assert rows[2][:2] == ["2023-07-14", "00:00:00"]


def test_transform_iso_8601_combined_datetime():
    raw = (
        b"station,DATE,tmp,dew\n"
        b"KDVO,2023-07-13T10:00:00Z,225,150\n"
        b"KDVO,2023-07-13T11:00:00Z,240,148\n"
    )
    header, rows = _transform_csv(raw)
    assert header == ["date", "time", "tmp", "dew"]
    assert rows[0] == ["2023-07-13", "10:00:00", "225", "150"]


def test_transform_semicolon_delimiter():
    raw = b"Date;Time;Temp\n2023-07-13;10:00:00;22.5\n2023-07-13;11:00:00;23.8\n"
    header, rows = _transform_csv(raw)
    assert header == ["date", "time", "temp"]
    assert rows[0] == ["2023-07-13", "10:00:00", "22.5"]


def test_transform_am_pm_time():
    raw = (
        b"Date,Time,Temp\n"
        b"7/13/2023,10:00:00 AM,22.5\n"
        b"7/13/2023,2:30:00 PM,28.1\n"
        b"7/13/2023,11:45:00 PM,18.2\n"
    )
    header, rows = _transform_csv(raw)
    assert rows[0][1] == "10:00:00"
    assert rows[1][1] == "14:30:00"
    assert rows[2][1] == "23:45:00"


def test_transform_content_based_fallback_when_headers_dont_match():
    """Headers don't contain 'date' or 'time' — must auto-detect by values."""
    raw = (
        b"Day,Clock,Temp\n"
        b"2023-07-13,10:00:00,22.5\n"
        b"2023-07-13,11:00:00,23.8\n"
    )
    header, rows = _transform_csv(raw)
    assert header[:2] == ["date", "time"]
    assert rows[0][0] == "2023-07-13"
    assert rows[0][1] == "10:00:00"


def test_transform_rejects_empty_file():
    with pytest.raises(Exception) as exc:
        _transform_csv(b"")
    assert "header row" in str(exc.value).lower()


def test_transform_rejects_no_date_column():
    raw = b"name,value\nfoo,1\nbar,2\n"
    with pytest.raises(Exception) as exc:
        _transform_csv(raw)
    assert "date" in str(exc.value).lower()


def test_transform_deduplicates_by_timestamp_keeps_last():
    """PyHelios silently drops duplicate timestamps. The transformer must
    deduplicate, keeping the last row for each (date, time) pair."""
    raw = (
        b"date,time,temp\n"
        b"2023-07-13,10:00:00,22.5\n"
        b"2023-07-13,10:00:00,99.9\n"
        b"2023-07-13,11:00:00,23.8\n"
    )
    header, rows = _transform_csv(raw)
    assert len(rows) == 2
    assert rows[0] == ["2023-07-13", "10:00:00", "99.9"]
    assert rows[1] == ["2023-07-13", "11:00:00", "23.8"]


def test_transform_drops_columns_with_non_numeric_values():
    raw = (
        b"date,time,good_col,bad_col\n"
        b"2023-07-13,10:00:00,1.5,hello\n"
        b"2023-07-13,11:00:00,2.5,world\n"
    )
    header, _ = _transform_csv(raw)
    assert "good_col" in header
    assert "bad_col" not in header


# ─────────────────────────── Upload endpoint ─────────────────────────────────


def test_upload_clean_csv_returns_success(client):
    session_id, project_id, scenario_id = _make_project(client)

    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        headers=_session_headers(session_id),
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 200
    assert r.json() == {"success": True, "row_count": 3, "column_count": 4}


def test_upload_cimis_csv_gets_transformed(client):
    session_id, project_id, scenario_id = _make_project(client)

    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        headers=_session_headers(session_id),
        files={"file": ("cimis.csv", CIMIS_CSV, "text/csv")},
    )

    assert r.status_code == 200
    # CIMIS output: date, time, stn_id, eto_mm, air_temp_c, rel_hum
    assert r.json() == {"success": True, "row_count": 3, "column_count": 6}


def test_upload_empty_file_returns_400(client):
    session_id, project_id, scenario_id = _make_project(client)

    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        headers=_session_headers(session_id),
        files={"file": ("empty.csv", b"", "text/csv")},
    )

    assert r.status_code == 400
    assert "empty" in r.text.lower()


def test_upload_invalid_csv_format_returns_400(client):
    session_id, project_id, scenario_id = _make_project(client)

    bad = b"name,value\nfoo,1\nbar,2\n"
    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        headers=_session_headers(session_id),
        files={"file": ("bad.csv", bad, "text/csv")},
    )

    assert r.status_code == 400


def test_upload_missing_session_id_header_returns_400(client):
    _, project_id, scenario_id = _make_project(client)

    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 400
    assert "session_id" in r.text.lower()


def test_upload_unknown_project_returns_404(client):
    session_id = f"session_{uuid4().hex[:8]}"
    bogus_project = uuid4().hex
    bogus_scenario = uuid4().hex

    r = client.post(
        _url(bogus_project, bogus_scenario, "uploadfile"),
        headers=_session_headers(session_id),
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_upload_with_wrong_session_returns_404(client):
    """A different user's session can't upload to a project they don't own."""
    owner_session, project_id, scenario_id = _make_project(client)
    intruder_session = f"session_{uuid4().hex[:8]}"
    assert intruder_session != owner_session

    r = client.post(
        _url(project_id, scenario_id, "uploadfile"),
        headers=_session_headers(intruder_session),
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 404


# ─────────────────────────── Read endpoints ──────────────────────────────────


def test_inspect_empty_scenario(client):
    session_id, project_id, scenario_id = _make_project(client)
    r = client.get(
        _url(project_id, scenario_id, "inspect"),
        headers=_session_headers(session_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert "pyhelios_available" in body
    assert body["file"] == {
        "exists": False,
        "note": "no file — content lives in PyHelios memory only",
    }


def test_get_all_timeseries_data_empty_scenario(client):
    session_id, project_id, scenario_id = _make_project(client)
    r = client.get(
        _url(project_id, scenario_id, "getAllTimeSeriesData"),
        headers=_session_headers(session_id),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["rows"] == []
    assert body["row_count"] == 0
    assert body["total_rows"] == 0
    assert body["labels"] == ["date", "time"]


def test_get_all_timeseries_data_accepts_paging_params(client):
    session_id, project_id, scenario_id = _make_project(client)
    r = client.get(
        _url(project_id, scenario_id, "getAllTimeSeriesData"),
        headers=_session_headers(session_id),
        params={"limit": 10, "offset": 5},
    )
    assert r.status_code == 200


# ─────────────────────────── Delete wipe-all ─────────────────────────────────


def test_delete_wipe_all_returns_success(client):
    session_id, project_id, scenario_id = _make_project(client)
    r = client.post(
        _url(project_id, scenario_id, "delete"),
        headers=_session_headers(session_id),
        json={},
    )
    # 200 if PyHelios is available, 503 otherwise
    assert r.status_code in (200, 503)


# ─────────────────────────── /addCol ────────────────────────────────────────
#
# Column flow (doc Section 3.2):
#   - body.column is a LIST of {name, datatype, data_unit, values:[{date,time,value}]}
#   - Each column persists a row in `weather_data_headers` AND writes its cells
#     into PyHelios under label = str(header.id).
#   - Single transaction wraps all N columns; partial failure rolls back.
#   - Empty list -> 400. Duplicate names in body -> 422. Existing name -> 409.


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


def _headers_url(project_id: str, scenario_id: str) -> str:
    return f"/api/weather/project/{project_id}/scenario/{scenario_id}/weather_data_header"


def test_add_single_column_returns_id_and_persists_header(client):
    """One column wrapped in [...] -> 200 with columns:[{id,name,...}], header
    visible via GET /weather_data_header, cells under label str(id)."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "temp",
            "datatype": dt,
            "data_unit": u,
            "values": [
                {"date": "2024-01-01", "time": "10:00:00", "value": "20.5"},
                {"date": "2024-01-01", "time": "11:00:00", "value": "21.0"},
            ],
        }]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert len(body["columns"]) == 1
    col = body["columns"][0]
    assert col["name"] == "temp"
    assert col["datatype_id"] == dt
    assert col["data_unit_id"] == u
    assert isinstance(col["id"], int)

    # Header is now visible in the per-scenario header set.
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    assert r.status_code == 200
    names = [h["name"] for h in r.json()["headers"]]
    assert "temp" in names

    # PyHelios stores the cells under str(header.id), NOT the user-facing name.
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    assert r.status_code == 200
    labels = r.json()["labels"]
    assert str(col["id"]) in labels
    assert "temp" not in labels  # user name is not the storage key


def test_add_multi_column_persists_all_in_order(client):
    """Multi-column add is one atomic call. display_order monotonic."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u1 = _make_data_unit(client, dt)
    u2 = _make_data_unit(client, dt)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "a", "datatype": dt, "data_unit": u1, "values": []},
            {"name": "b", "datatype": dt, "data_unit": u2, "values": []},
        ]},
    )
    assert r.status_code == 200, r.text
    cols = r.json()["columns"]
    assert [c["name"] for c in cols] == ["a", "b"]

    # Display order in the header set is incremental.
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    headers = r.json()["headers"]
    by_name = {h["name"]: h for h in headers}
    assert by_name["a"]["display_order"] < by_name["b"]["display_order"]


def test_add_column_with_null_metadata_persists_header_anyway(client):
    """datatype/data_unit are optional. Column still persists with NULL FKs —
    used when the frontend adds a column before the user has chosen its
    catalog mapping."""
    sid, pid, scn = _make_project(client)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "raw", "values": []}]},
    )
    assert r.status_code == 200, r.text
    col = r.json()["columns"][0]
    assert col["datatype_id"] is None
    assert col["data_unit_id"] is None


def test_add_empty_column_list_returns_400(client):
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": []},
    )
    assert r.status_code == 400


def test_add_duplicate_names_in_body_returns_422(client):
    """Pydantic _no_dup_column_names validator catches this before the service."""
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "dup", "values": []},
            {"name": "dup", "values": []},
        ]},
    )
    assert r.status_code == 422


def test_add_existing_column_name_returns_409(client):
    """Adding a column whose name collides with an existing header -> 409."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "temp", "datatype": dt, "data_unit": u, "values": []}]},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "temp", "datatype": dt, "data_unit": u, "values": []}]},
    )
    assert r.status_code == 409
    assert "already exists" in r.json()["detail"].lower()


def test_add_reserved_name_returns_400(client):
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "date", "values": []}]},
    )
    assert r.status_code == 400
    assert "reserved" in r.json()["detail"].lower()


def test_add_unknown_datatype_returns_404(client):
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "x", "datatype": 99999999, "data_unit": u, "values": []
        }]},
    )
    assert r.status_code == 404


def test_add_unknown_data_unit_returns_404(client):
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "x", "datatype": dt, "data_unit": 99999999, "values": []
        }]},
    )
    assert r.status_code == 404


def test_add_unit_does_not_belong_to_datatype_returns_400(client):
    """Service-level invariant: data_unit.data_type_id must equal datatype."""
    sid, pid, scn = _make_project(client)
    dt1 = _make_data_type(client)
    dt2 = _make_data_type(client)
    u_of_dt2 = _make_data_unit(client, dt2)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "x", "datatype": dt1, "data_unit": u_of_dt2, "values": []
        }]},
    )
    assert r.status_code == 400
    assert "belongs to" in r.json()["detail"].lower()


def test_add_partial_failure_rolls_back_first_column(client):
    """If column[1] fails validation, column[0] must NOT be persisted —
    transaction is all-or-nothing."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # First call: install one valid column so we have something to compare against.
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "preexisting", "datatype": dt, "data_unit": u, "values": []}]},
    )
    assert r.status_code == 200

    # Second call: column[0] is fine, column[1] has unknown datatype -> whole call fails.
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "good", "datatype": dt, "data_unit": u, "values": []},
            {"name": "bad",  "datatype": 99999999, "data_unit": u, "values": []},
        ]},
    )
    assert r.status_code == 404

    # Neither "good" nor "bad" must exist; only "preexisting" survives.
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    names = [h["name"] for h in r.json()["headers"]]
    assert names == ["preexisting"]


def test_add_bad_value_format_returns_400_and_does_not_persist(client):
    """A malformed cell in the values array fails the whole add."""
    sid, pid, scn = _make_project(client)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "x",
            "values": [
                {"date": "not-a-date", "time": "10:00:00", "value": "1.0"},
            ],
        }]},
    )
    assert r.status_code == 400

    # No header was created.
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    assert "x" not in [h["name"] for h in r.json()["headers"]]


def test_add_empty_values_creates_column_with_no_cells(client):
    """A column with values:[] is legal — the metadata row is the point."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "metadata_only", "datatype": dt, "data_unit": u, "values": []
        }]},
    )
    assert r.status_code == 200, r.text

    # Cells: PyHelios still sees a label (column exists) but its length is 0.
    col_id = r.json()["columns"][0]["id"]
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    body = r.json()
    # The label exists either way; row_count for an empty column is 0.
    # PyHelios may not register a label until the first cell is written.
    # Either way, no rows were appended.
    assert body["row_count"] == 0
    # Sanity: header row exists.
    assert col_id in [h["id"] for h in
                      client.get(_headers_url(pid, scn),
                                 headers=_session_headers(sid)).json()["headers"]]


# ─────────────────────────── /addRow ────────────────────────────────────────
#
# Row flow:
#   - body.rows is a list of {date, time, <label>: value} dicts.
#   - Labels must be the str(header.id) values returned by /addCol.
#   - PyHelios silently drops dup timestamps, so we reject upfront (within
#     batch and against existing rows).


def _add_one_column(client, sid, pid, scn) -> tuple[int, int]:
    """Install two columns and seed each with one cell so PyHelios registers
    their labels. Without a seed, PyHelios doesn't expose the label and the
    row branch's label-set check would reject any subsequent /addRow.

    Returns the two header ids; cells live under str(id)."""
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    seed = [{"date": "2023-01-01", "time": "00:00:00", "value": "0"}]
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "a", "datatype": dt, "data_unit": u, "values": seed},
            {"name": "b", "datatype": dt, "data_unit": u, "values": seed},
        ]},
    )
    assert r.status_code == 200, r.text
    cols = r.json()["columns"]
    return cols[0]["id"], cols[1]["id"]


def test_addrow_single_row_appends_to_pyhelios(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)
    # _add_one_column seeded 1 row at 2023-01-01 00:00:00.

    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["added_rows"] == 1
    assert body["row_count"] == 2  # seed + new row

    # Verify it landed.
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    assert r.json()["row_count"] == 2


def test_addrow_multiple_rows_in_one_call(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0"},
            {"date": "2024-01-01", "time": "11:00:00", str(a_id): "3.0", str(b_id): "4.0"},
        ]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["added_rows"] == 2
    assert body["row_count"] == 3  # seed + 2 new rows


def test_addrow_missing_date_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0"},
        ]},
    )
    assert r.status_code == 400
    assert "date is required" in r.json()["detail"].lower()


def test_addrow_missing_time_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", str(a_id): "1.0", str(b_id): "2.0"},
        ]},
    )
    assert r.status_code == 400
    assert "time is required" in r.json()["detail"].lower()


def test_addrow_duplicate_timestamp_in_batch_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0"},
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "9.0", str(b_id): "8.0"},
        ]},
    )
    assert r.status_code == 400
    assert "duplicate timestamp in batch" in r.json()["detail"].lower()


def test_addrow_existing_timestamp_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    # Seed one row.
    client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0"},
        ]},
    )

    # Same timestamp again → 400.
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "9.0", str(b_id): "8.0"},
        ]},
    )
    assert r.status_code == 400
    assert "already exists" in r.json()["detail"].lower()


def test_addrow_label_mismatch_returns_400(client):
    """Row labels (excluding date/time) must exactly match the existing
    PyHelios label set — no missing, no extras."""
    sid, pid, scn = _make_project(client)
    a_id, b_id = _add_one_column(client, sid, pid, scn)

    # Missing b_id.
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0"},
        ]},
    )
    assert r.status_code == 400
    assert "must match" in r.json()["detail"].lower()

    # Unknown label.
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(a_id): "1.0", str(b_id): "2.0", "ghost": "0"},
        ]},
    )
    assert r.status_code == 400


def test_addrow_unknown_scenario_returns_404(client):
    sid = f"session_{uuid4().hex[:8]}"
    r = client.post(
        f"/api/weather/project/{uuid4().hex}/scenario/{uuid4().hex}/addRow",
        headers=_session_headers(sid),
        json={"rows": [{"date": "2024-01-01", "time": "10:00:00"}]},
    )
    assert r.status_code == 404
