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
    header, rows, _ = _transform_csv(CLEAN_CSV)
    assert header == ["date", "time", "temperature", "humidity"]
    assert rows[0] == ["2023-07-13", "10:00:00", "22.5", "65"]
    assert len(rows) == 3


def test_transform_cimis_format():
    """Split date+HHMM, qc flag columns, Stn Name as text, '2400' rollover."""
    header, rows, _ = _transform_csv(CIMIS_CSV)

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
    header, rows, _ = _transform_csv(raw)
    assert header == ["date", "time", "tmp", "dew"]
    assert rows[0] == ["2023-07-13", "10:00:00", "225", "150"]


def test_transform_semicolon_delimiter():
    raw = b"Date;Time;Temp\n2023-07-13;10:00:00;22.5\n2023-07-13;11:00:00;23.8\n"
    header, rows, _ = _transform_csv(raw)
    assert header == ["date", "time", "temp"]
    assert rows[0] == ["2023-07-13", "10:00:00", "22.5"]


def test_transform_am_pm_time():
    raw = (
        b"Date,Time,Temp\n"
        b"7/13/2023,10:00:00 AM,22.5\n"
        b"7/13/2023,2:30:00 PM,28.1\n"
        b"7/13/2023,11:45:00 PM,18.2\n"
    )
    header, rows, _ = _transform_csv(raw)
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
    header, rows, _ = _transform_csv(raw)
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
    header, rows, _ = _transform_csv(raw)
    assert len(rows) == 2
    assert rows[0] == ["2023-07-13", "10:00:00", "99.9"]
    assert rows[1] == ["2023-07-13", "11:00:00", "23.8"]


def test_transform_drops_columns_with_non_numeric_values():
    raw = (
        b"date,time,good_col,bad_col\n"
        b"2023-07-13,10:00:00,1.5,hello\n"
        b"2023-07-13,11:00:00,2.5,world\n"
    )
    header, _, _ = _transform_csv(raw)
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


# ─────────────────────────── Delete one row ─────────────────────────────────
#
# Row delete physically removes the (date, time) point from EVERY column via
# PyHelios deleteTimeseriesDataPoint (helios-core >= v1.3.73). These tests are
# skipped when the built native lib predates that method, so CI stays green
# until PyHelios is rebuilt — then they verify the real behaviour.


def _row_delete_supported() -> bool:
    """True only if the loaded PyHelios lib exports deleteTimeseriesDataPoint."""
    try:
        from app.helios import context as helios_ctx
        if not helios_ctx.PYHELIOS_AVAILABLE:
            return False
        from pyhelios.wrappers.UContextWrapper import (
            _TIMESERIES_DELETE_POINT_AVAILABLE,
        )
        return bool(_TIMESERIES_DELETE_POINT_AVAILABLE)
    except Exception:
        return False


requires_row_delete = pytest.mark.skipif(
    not _row_delete_supported(),
    reason="PyHelios lib lacks deleteTimeseriesDataPoint — rebuild to helios-core >= v1.3.73",
)


def _seed_two_columns_three_rows(client, sid, pid, scn) -> tuple[int, int]:
    """Two columns ('a', 'b'), each with cells at three timestamps. Returns
    the two header ids. Used to assert a row delete removes the timestamp
    from BOTH columns."""
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    vals = [
        {"date": "2024-01-01", "time": "10:00:00", "value": "1.0"},
        {"date": "2024-01-01", "time": "11:00:00", "value": "2.0"},
        {"date": "2024-01-01", "time": "12:00:00", "value": "3.0"},
    ]
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "a", "datatype": dt, "data_unit": u, "values": vals},
            {"name": "b", "datatype": dt, "data_unit": u, "values": vals},
        ]},
    )
    assert r.status_code == 200, r.text
    cols = r.json()["columns"]
    return cols[0]["id"], cols[1]["id"]


@requires_row_delete
def test_delete_rows_removes_them_and_drops_row_count(client):
    """POST /deleteRow with a list removes each timestamp from every column;
    row_count drops by the number deleted and survivors keep their values."""
    sid, pid, scn = _make_project(client)
    a_id, b_id = _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[
            {"date": "2024-01-01", "time": "10:00:00"},
            {"date": "2024-01-01", "time": "12:00:00"},
        ],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["deleted_rows"] == 2
    assert body["row_count"] == 1  # was 3
    assert body["message"] == "2 rows has been deleted"

    # Only the middle row survives, in both columns, with its real value.
    r = client.get(_url(pid, scn, "getAllTimeSeriesData"), headers=_session_headers(sid))
    data = r.json()
    assert [row["time"] for row in data["rows"]] == ["11:00:00"]
    survivor = data["rows"][0]
    assert survivor[str(a_id)] == pytest.approx(2.0)
    assert survivor[str(b_id)] == pytest.approx(2.0)


@requires_row_delete
def test_delete_single_row_via_list(client):
    """A list of one deletes that row; the message is singular."""
    sid, pid, scn = _make_project(client)
    _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[{"date": "2024-01-01", "time": "11:00:00"}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["deleted_rows"] == 1
    assert r.json()["row_count"] == 2
    assert r.json()["message"] == "1 row has been deleted"


@requires_row_delete
def test_delete_missing_row_returns_404(client):
    """A row that doesn't exist returns 404 (listing it); nothing is removed."""
    sid, pid, scn = _make_project(client)
    _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[{"date": "2099-12-31", "time": "23:59:59"}],
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["not_found"] == [{"date": "2099-12-31", "time": "23:59:59"}]

    # nothing was removed
    r = client.get(_url(pid, scn, "getAllTimeSeriesData"), headers=_session_headers(sid))
    assert r.json()["row_count"] == 3


@requires_row_delete
def test_delete_rows_partial_missing_is_atomic_404(client):
    """If ANY requested row is missing, the whole call 404s and deletes nothing
    — even the rows that did exist stay put."""
    sid, pid, scn = _make_project(client)
    _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[
            {"date": "2024-01-01", "time": "10:00:00"},   # exists
            {"date": "2099-12-31", "time": "23:59:59"},   # missing
        ],
    )
    assert r.status_code == 404, r.text
    assert r.json()["detail"]["not_found"] == [{"date": "2099-12-31", "time": "23:59:59"}]

    # the existing row was NOT deleted — atomic
    r = client.get(_url(pid, scn, "getAllTimeSeriesData"), headers=_session_headers(sid))
    assert r.json()["row_count"] == 3


@requires_row_delete
def test_delete_rows_empty_list_returns_400(client):
    sid, pid, scn = _make_project(client)
    _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[],
    )
    assert r.status_code == 400, r.text
    assert "empty" in r.json()["detail"].lower()


@requires_row_delete
def test_delete_last_row_leaves_columns_present(client):
    """Deleting the only row drops row_count to 0 but the column headers
    survive in SQL (so the table structure persists on reload)."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "only", "datatype": dt, "data_unit": u,
            "values": [{"date": "2024-01-01", "time": "10:00:00", "value": "1.0"}],
        }]},
    )
    assert r.status_code == 200, r.text

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[{"date": "2024-01-01", "time": "10:00:00"}],
    )
    assert r.status_code == 200, r.text
    assert r.json()["row_count"] == 0

    # Column header still exists.
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    assert "only" in [h["name"] for h in r.json()["headers"]]


@requires_row_delete
def test_delete_row_then_readd_same_timestamp_succeeds(client):
    """Regression guard: the old NaN-blank delete left the timestamp in place,
    so re-adding it failed with 'already exists'. True removal fixes that."""
    sid, pid, scn = _make_project(client)
    a_id, b_id = _seed_two_columns_three_rows(client, sid, pid, scn)

    r = client.post(
        _url(pid, scn, "deleteRow"),
        headers=_session_headers(sid),
        json=[{"date": "2024-01-01", "time": "11:00:00"}],
    )
    assert r.status_code == 200, r.text

    # The same timestamp can now be re-added (no zombie row blocking it).
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "11:00:00", str(a_id): "9.0", str(b_id): "8.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added_rows"] == 1
    assert r.json()["row_count"] == 3


# ─────────────────────────── /update — batch cell updates ──────────────────


def _seed_two_cell_column(client, sid, pid, scn) -> tuple[int, int]:
    """Create two columns, each with two cells at known timestamps.
    Returns the two header ids."""
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)
    seed = [
        {"date": "2024-01-01", "time": "10:00:00", "value": "1.0"},
        {"date": "2024-01-01", "time": "11:00:00", "value": "2.0"},
    ]
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


def test_update_single_cell(client):
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "99.5"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "updated_count": 1}


def test_update_many_cells_in_one_call(client):
    """Mirrors the data-unit conversion case: many cells under the same
    column rewritten in a single round-trip."""
    sid, pid, scn = _make_project(client)
    a_id, b_id = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "33.8"},
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "11:00:00"}, "value": "35.6"},
            {"col": str(b_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "100.0"},
            {"col": str(b_id), "row": {"date": "2024-01-01", "time": "11:00:00"}, "value": "200.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json() == {"success": True, "updated_count": 4}


def test_update_empty_list_returns_400(client):
    sid, pid, scn = _make_project(client)
    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": []},
    )
    assert r.status_code == 400
    assert "cannot be empty" in r.json()["detail"].lower()


def test_update_reserved_column_name_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "1.0"},
            {"col": "date",   "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "2.0"},
        ]},
    )
    assert r.status_code == 400
    assert "updates[1]" in r.json()["detail"]


def test_update_unknown_column_returns_404(client):
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": "99999999", "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "1.0"},
        ]},
    )
    assert r.status_code == 404
    assert "updates[0]" in r.json()["detail"]


def test_update_unknown_cell_returns_404(client):
    """Cell at (col, date, time) not present → 404 from PyHelios."""
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2099-12-31", "time": "23:59:59"}, "value": "1.0"},
        ]},
    )
    assert r.status_code == 404


def test_update_non_numeric_value_returns_400(client):
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "hello"},
        ]},
    )
    assert r.status_code == 400


def test_update_partial_failure_leaves_earlier_items_applied(client):
    """Fail-fast: items processed before the failing one stay applied
    (PyHelios isn't transactional). Documents the contract."""
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {"col": str(a_id), "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "77.7"},
            {"col": "99999999", "row": {"date": "2024-01-01", "time": "10:00:00"}, "value": "1.0"},
        ]},
    )
    assert r.status_code == 404
    assert "updates[1]" in r.json()["detail"]

    # First item already applied — verify by reading the row back
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    rows = r.json()["rows"]
    matching = next(
        row for row in rows
        if row["date"] == "2024-01-01" and row["time"] == "10:00:00"
    )
    # PyHelios stores 32-bit floats; round-trip loses precision.
    assert matching[str(a_id)] == pytest.approx(77.7)


# ─────────────────────────── /clear_data — clear both stores ────────────────


def test_clear_data_clears_headers_and_pyhelios(client):
    """DELETE /clear_data removes every weather_data_headers row for the
    scenario AND clears PyHelios timeseries data. After: no headers, no rows."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Seed two columns with cells so both stores have content
    client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "a", "datatype": dt, "data_unit": u,
             "values": [{"date": "2024-01-01", "time": "10:00:00", "value": "1"}]},
            {"name": "b", "datatype": dt, "data_unit": u,
             "values": [{"date": "2024-01-01", "time": "10:00:00", "value": "2"}]},
        ]},
    )

    # Sanity: data is there before
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    assert r.json()["count"] == 2

    # Clear
    r = client.delete(_url(pid, scn, "clear_data"), headers=_session_headers(sid))
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["headers_removed"] == 2
    assert body["row_count"] == 0
    assert body["column_count"] == 2

    # SQL: header set is empty
    r = client.get(_headers_url(pid, scn), headers=_session_headers(sid))
    assert r.json()["count"] == 0

    # PyHelios: no rows left
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    assert r.json()["row_count"] == 0


def test_clear_data_on_empty_scenario_returns_zero(client):
    """Clearing a scenario that has nothing is a clean no-op."""
    sid, pid, scn = _make_project(client)
    r = client.delete(_url(pid, scn, "clear_data"), headers=_session_headers(sid))
    assert r.status_code == 200
    assert r.json() == {
        "success": True,
        "headers_removed": 0,
        "row_count": 0,
        "column_count": 2,
    }


def test_clear_data_unknown_scenario_returns_404(client):
    sid = f"session_{uuid4().hex[:8]}"
    r = client.delete(
        f"/api/weather/project/{uuid4().hex}/scenario/{uuid4().hex}/clear_data",
        headers=_session_headers(sid),
    )
    assert r.status_code == 404


def test_clear_data_from_another_session_returns_404(client):
    _, pid, scn = _make_project(client)
    sid_intruder = f"session_{uuid4().hex[:8]}"

    r = client.delete(_url(pid, scn, "clear_data"), headers=_session_headers(sid_intruder))
    assert r.status_code == 404


def test_clear_data_does_not_affect_other_scenarios(client):
    """Clearing scenario A leaves scenario B intact."""
    sid, pid, scn_a = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Create a second scenario
    r = client.post(
        f"/api/project/{pid}/scenarios/create",
        json={"name": "second"},
        headers=_session_headers(sid),
    )
    scn_b = r.json()["scenario_id"]

    # Seed both scenarios
    for s in (scn_a, scn_b):
        client.post(
            _url(pid, s, "addCol"),
            headers=_session_headers(sid),
            json={"column": [{
                "name": "x", "datatype": dt, "data_unit": u,
                "values": [{"date": "2024-01-01", "time": "10:00:00", "value": "1"}],
            }]},
        )

    # Clear scenario A only
    r = client.delete(_url(pid, scn_a, "clear_data"), headers=_session_headers(sid))
    assert r.status_code == 200

    # A is empty
    r = client.get(_headers_url(pid, scn_a), headers=_session_headers(sid))
    assert r.json()["count"] == 0

    # B still has its column
    r = client.get(_headers_url(pid, scn_b), headers=_session_headers(sid))
    assert r.json()["count"] == 1


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
    """Install two columns with a seed cell each. The seed timestamp lets
    tests that assert row_count have a known starting baseline. Returns
    the two header ids; PyHelios cells live under str(id).

    Note: the SEED is only needed for tests that check row_count. /addRow
    works against empty-addCol columns too — see
    test_addrow_against_empty_addcol_no_seed_required for that case."""
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


def test_addrow_against_empty_addcol_no_seed_required(client):
    """A column added via /addCol with values=[] must still be a legal
    /addRow target — the SQL header drives the label set, not PyHelios's
    listTimeseriesVariables()."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # /addCol with no values: SQL header exists, PyHelios has no label yet
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [
            {"name": "empty_col", "datatype": dt, "data_unit": u, "values": []},
        ]},
    )
    assert r.status_code == 200, r.text
    col_id = r.json()["columns"][0]["id"]

    # /addRow that references it must succeed
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(col_id): "42.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["added_rows"] == 1
    assert body["column_count"] == 3   # date + time + 1 SQL header


def test_addcol_and_addrow_accept_hhmm_time_format(client):
    """Time strings without seconds (HH:MM) are normalized to HH:MM:00.
    Browser <input type="time"> emits HH:MM by default; CIMIS and many
    weather feeds also use it."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # /addCol with HH:MM time in the seed cell
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "temp",
            "datatype": dt,
            "data_unit": u,
            "values": [{"date": "2024-01-01", "time": "10:00", "value": "20.0"}],
        }]},
    )
    assert r.status_code == 200, r.text
    col_id = r.json()["columns"][0]["id"]

    # /addRow with HH:MM time
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "11:00", str(col_id): "21.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added_rows"] == 1


def test_addrow_ignores_headers_named_date_or_time(client):
    """The bulk PUT /weather_data_header doesn't enforce the reserved-name
    rule. If a header named 'date' or 'time' slipped in, /addRow must
    NOT include its id in the required label set."""
    sid, pid, scn = _make_project(client)
    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    # Sideload a header with a reserved name via bulk PUT (addCol would
    # reject this, but bulk PUT doesn't).
    r = client.put(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers=_session_headers(sid),
        json={"headers": [
            {"name": "date", "helios_data_type_id": dt, "unit_id": u, "display_order": 0},
            {"name": "real", "helios_data_type_id": dt, "unit_id": u, "display_order": 1},
        ]},
    )
    assert r.status_code == 200, r.text
    real_id = next(h["id"] for h in r.json()["headers"] if h["name"] == "real")

    # /addRow with only the "real" header — must succeed; the "date" header
    # is filtered out of the required label set.
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {"date": "2024-01-01", "time": "10:00:00", str(real_id): "1.0"},
        ]},
    )
    assert r.status_code == 200, r.text
    assert r.json()["added_rows"] == 1


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


# ─────────────────────────── addCol default_value ────────────────────────────


def test_addcol_default_value_fills_all_timestamps_when_values_empty(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "with_default",
            "values": [],
            "default_value": 42.0,
        }]},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["columns"][0]["id"]
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    body = r.json()
    assert body["row_count"] == 3
    for row in body["rows"]:
        assert row[str(new_id)] == 42.0


def test_addcol_default_value_with_values_fills_only_missing_timestamps(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "mixed",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "100"},
            ],
            "default_value": 7,
        }]},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["columns"][0]["id"]
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    rows = r.json()["rows"]
    by_time = {row["time"]: row[str(new_id)] for row in rows}
    assert by_time["10:00:00"] == 100.0
    assert by_time["11:00:00"] == 7.0
    assert by_time["12:00:00"] == 7.0


def test_addcol_default_value_silent_noop_when_no_timestamps(client):
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "no_rows_yet",
            "values": [],
            "default_value": 99,
        }]},
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/weather_data_header",
        headers=_session_headers(sid),
    )
    assert any(h["name"] == "no_rows_yet" for h in r.json()["headers"])


def test_addcol_default_value_numeric_string_accepted(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "str_default",
            "values": [],
            "default_value": "5.5",
        }]},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["columns"][0]["id"]
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    for row in r.json()["rows"]:
        assert row[str(new_id)] == 5.5


def test_addcol_default_value_non_numeric_returns_422(client):
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "bad",
            "values": [],
            "default_value": "not-a-number",
        }]},
    )
    assert r.status_code == 422


def test_addcol_duplicate_date_time_returns_400_with_friendly_message(client):
    """A column with two cells at the same (date, time) used to surface
    PyHelios's opaque 'Failed to insert timeseries data for unknown reason'
    wrapped in a 500. We now pre-detect and return a 400 with a clean
    user-facing message."""
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "temperature",
            "values": [
                {"date": "2026-05-21", "time": "10:00:00", "value": "20"},
                {"date": "2026-05-21", "time": "10:00:00", "value": "22"},
            ],
        }]},
    )
    assert r.status_code == 400, r.text
    detail = r.json()["detail"].lower()
    assert "duplicate" in detail
    assert "date-time" in detail


# ─────────────────────────── PATCH /updateCol ────────────────────────────────


def _seed_scenario_with_one_column(client):
    """Scenario with 3 timestamps + one SQL-tracked column 'temperature'.

    Uses addCol so the SQL `weather_data_headers` row exists alongside the
    PyHelios cells.
    """
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "temperature",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "22.5"},
                {"date": "2023-07-13", "time": "11:00:00", "value": "23.8"},
                {"date": "2023-07-13", "time": "12:00:00", "value": "25.3"},
            ],
        }]},
    )
    assert r.status_code == 200, r.text
    return sid, pid, scn, {"temperature": r.json()["columns"][0]["id"]}


def test_updatecol_with_values_overwrites_existing_cells(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "111"},
                {"date": "2023-07-13", "time": "11:00:00", "value": "222"},
            ],
        },
    )
    assert r.status_code == 200, r.text
    assert r.json()["columns"][0]["id"] == temp_id
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(temp_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] == 111.0
    assert by_time["11:00:00"] == 222.0
    assert by_time["12:00:00"] is None


def test_updatecol_default_value_fills_missing_cells_only(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "extra", "values": []}]},
    )
    extra_id = r.json()["columns"][0]["id"]
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{extra_id}",
        headers=_session_headers(sid),
        json={
            "name": "extra",
            "values": [],
            "default_value": 50,
        },
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    for row in r.json()["rows"]:
        assert row[str(extra_id)] == 50.0


def test_updatecol_default_value_overwrites_existing_cells(client):
    """default_value sets every scenario timestamp's cell to the default,
    overwriting any existing value. Use case: select-all / deselect-all on
    a check column with one PATCH call."""
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [],
            "default_value": 999,
        },
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    # Every cell now holds 999 (overwritten — was 22.5, 23.8, 25.3).
    for row in r.json()["rows"]:
        assert row[str(temp_id)] == 999.0


def test_updatecol_explicit_values_win_over_default(client):
    """When both `values[]` and `default_value` are given, listed timestamps
    keep their explicit value; everything else gets default_value (overwriting)."""
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "111"},
            ],
            "default_value": 5,
        },
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(temp_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] == 111.0   # explicit wins
    assert by_time["11:00:00"] == 5.0     # default overwrote 23.8
    assert by_time["12:00:00"] == 5.0     # default overwrote 25.3


def test_updatecol_values_plus_default_value_together(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "extra", "values": []}]},
    )
    extra_id = r.json()["columns"][0]["id"]
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{extra_id}",
        headers=_session_headers(sid),
        json={
            "name": "extra",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "1"},
            ],
            "default_value": 9,
        },
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(extra_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] == 1.0
    assert by_time["11:00:00"] == 9.0
    assert by_time["12:00:00"] == 9.0


def test_updatecol_unknown_column_returns_404(client):
    sid, pid, scn = _make_project(client)
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/999999",
        headers=_session_headers(sid),
        json={
            "name": "nonexistent",
            "values": [{"date": "2024-01-01", "time": "10:00:00", "value": "1"}],
        },
    )
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


def test_updatecol_reserved_name_returns_400(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={"name": "date", "values": []},
    )
    assert r.status_code == 400


def test_updatecol_updates_datatype_and_unit_in_place(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]
    r = client.get("/api/data-types/")
    dt = next(t for t in r.json()["data_types"] if t["data_type"] == "air_temperature")
    base_unit_id = next(u["id"] for u in dt["units"] if u["is_base"])
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "datatype": dt["id"],
            "data_unit": base_unit_id,
        },
    )
    assert r.status_code == 200
    body = r.json()["columns"][0]
    assert body["id"] == temp_id
    assert body["datatype_id"] == dt["id"]
    assert body["data_unit_id"] == base_unit_id


def test_updatecol_inconsistent_unit_and_datatype_returns_400(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    r = client.get("/api/data-types/")
    by_name = {t["data_type"]: t for t in r.json()["data_types"]}
    temp_dt = by_name["air_temperature"]
    pressure_dt = by_name["air_pressure"]
    pressure_unit = next(u["id"] for u in pressure_dt["units"] if u["is_base"])
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{ids['temperature']}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "datatype": temp_dt["id"],
            "data_unit": pressure_unit,
        },
    )
    assert r.status_code == 400
    assert "belongs to" in r.json()["detail"].lower()


def test_updatecol_bad_date_format_returns_400(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{ids['temperature']}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [{"date": "13-07-2023", "time": "10:00:00", "value": "1"}],
        },
    )
    assert r.status_code == 400


def test_updatecol_non_numeric_value_returns_400(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{ids['temperature']}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [{"date": "2023-07-13", "time": "10:00:00", "value": "hello"}],
        },
    )
    assert r.status_code == 400


def test_updatecol_creates_cell_at_new_timestamp(client):
    sid, pid, scn = _make_project(client)
    client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("seed.csv", CLEAN_CSV, "text/csv")},
    )
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "newcol", "values": []}]},
    )
    new_id = r.json()["columns"][0]["id"]
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{new_id}",
        headers=_session_headers(sid),
        json={
            "name": "newcol",
            "values": [{"date": "2023-07-13", "time": "10:00:00", "value": "42"}],
        },
    )
    assert r.status_code == 200, r.text
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(new_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] == 42.0


def test_updatecol_other_session_returns_404(client):
    sid_a, pid, scn, ids = _seed_scenario_with_one_column(client)
    sid_b = f"session_{uuid4().hex[:8]}"
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{ids['temperature']}",
        headers=_session_headers(sid_b),
        json={"name": "temperature", "values": []},
    )
    assert r.status_code == 404


def test_updatecol_missing_session_id_returns_400(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{ids['temperature']}",
        json={"name": "temperature", "values": []},
    )
    assert r.status_code == 400


# ─────────────────────────── NaN-fill rule (uniform empty handling) ─────────


def test_addcol_with_no_values_no_default_fills_scenario_with_nan(client):
    """A new column added with empty values[] and no default_value gets a
    NaN cell at every existing scenario timestamp — keeps the column aligned
    with the scenario's row count instead of leaving it sparse."""
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)

    # Add a new empty column.
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "empty_col", "values": []}]},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["columns"][0]["id"]

    # All scenario timestamps should have a cell — NaN renders as JSON null.
    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    assert r.json()["row_count"] == 3
    for row in r.json()["rows"]:
        assert row[str(new_id)] is None  # NaN -> null


def test_addcol_with_explicit_empty_value_writes_nan(client):
    """An entry in values[] with value="" writes a NaN cell (not skipped)."""
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "mixed",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": ""},
                {"date": "2023-07-13", "time": "11:00:00", "value": "26"},
            ],
        }]},
    )
    assert r.status_code == 200, r.text
    new_id = r.json()["columns"][0]["id"]

    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(new_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] is None       # explicit empty -> NaN
    assert by_time["11:00:00"] == 26.0       # explicit value
    assert by_time["12:00:00"] is None       # not in values[], no default -> NaN


def test_addcol_empty_scenario_no_default_creates_empty_column(client):
    """Sanity: a new column on a scenario with NO existing rows AND no
    default_value AND no values[] creates an empty column (no cells)."""
    sid, pid, scn = _make_project(client)

    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{"name": "empty", "values": []}]},
    )
    assert r.status_code == 200, r.text


def test_updatecol_no_values_no_default_fills_missing_with_nan_only(client):
    """PATCH /updateCol with empty values[] and no default_value should:
       - leave existing cells alone (non-destructive)
       - fill missing scenario timestamps with NaN
    This keeps the column aligned without destroying user data."""
    sid, pid, scn = _make_project(client)
    # Seed a 3-timestamp scenario.
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "anchor",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "1"},
                {"date": "2023-07-13", "time": "11:00:00", "value": "2"},
                {"date": "2023-07-13", "time": "12:00:00", "value": "3"},
            ],
        }]},
    )
    # Add a column with one cell out of three.
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "partial",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": "100"},
            ],
        }]},
    )
    partial_id = r.json()["columns"][0]["id"]
    # Note: addCol now ALSO fills missing timestamps with NaN, so partial
    # already has 3 cells: 100, NaN, NaN. The PATCH below should be a no-op.

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{partial_id}",
        headers=_session_headers(sid),
        json={"name": "partial", "values": []},
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(partial_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] is None       # overwritten by default_value="NAN"
    assert by_time["11:00:00"] is None       # was NaN, still NaN
    assert by_time["12:00:00"] is None       # was NaN, still NaN


def test_updatecol_with_explicit_empty_value_writes_nan(client):
    """An empty value in updateCol values[] overwrites the existing cell with NaN."""
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{temp_id}",
        headers=_session_headers(sid),
        json={
            "name": "temperature",
            "values": [
                {"date": "2023-07-13", "time": "10:00:00", "value": ""},
            ],
        },
    )
    assert r.status_code == 200, r.text

    r = client.get(
        f"/api/weather/project/{pid}/scenario/{scn}/getAllTimeSeriesData",
        headers=_session_headers(sid),
    )
    by_time = {row["time"]: row[str(temp_id)] for row in r.json()["rows"]}
    assert by_time["10:00:00"] is None             # overwritten with NaN
    assert by_time["11:00:00"] is None             # overwritten by default_value="NAN"
    assert by_time["12:00:00"] is None             # overwritten by default_value="NAN"


def test_addCol_decimal_validation_limit_exceeded(client):
    sid, pid, scn = _make_project(client)
    r = client.post(
        _url(pid, scn, "addCol"),
        headers=_session_headers(sid),
        json={"column": [{
            "name": "humidity",
            "values": [
                {"date": "2024-01-01", "time": "10:00:00", "value": "1.12345678"} # 8 decimals
            ],
        }]},
    )
    assert r.status_code == 400
    assert "contains more than 7 decimal places" in r.text


def test_addrow_decimal_validation_limit_exceeded(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]
    
    r = client.post(
        _url(pid, scn, "addRow"),
        headers=_session_headers(sid),
        json={"rows": [
            {
                "date": "2024-01-01",
                "time": "10:00:00",
                str(temp_id): "1.12345678"  # 8 decimals
            }
        ]},
    )
    assert r.status_code == 400
    assert "contains more than 7 decimal places" in r.text


def test_updatecol_decimal_validation_limit_exceeded(client):
    sid, pid, scn, ids = _seed_scenario_with_one_column(client)
    temp_id = ids["temperature"]

    r = client.patch(
        _url(pid, scn, "update"),
        headers=_session_headers(sid),
        json={"updates": [
            {
                "col": str(temp_id),
                "row": {"date": "2023-07-13", "time": "10:00:00"},
                "value": "1.12345678" # 8 decimals
            }
        ]},
    )
    assert r.status_code == 400
    assert "contains more than 7 decimal places" in r.text


def test_upload_csv_decimal_truncation(client):
    sid, pid, scn = _make_project(client)
    # CSV with 8 decimal places
    csv_content = b"Date,Time,Air Temp\n2026-05-11,12:00:00,25.12345678\n"

    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("test.csv", csv_content, "text/csv")},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert "truncated" in body["message"].lower()

    # Verify that the value written is truncated
    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    rows = r.json()["rows"]
    assert len(rows) == 1
    # Check that value matches 25.1234567 (truncated, not rounded up)
    # Note: float comparison, but 25.1234567 should be close enough
    # PyHelios returns 32-bit float but the truncation happened beforehand.
    val = list(rows[0].values())[-1] # The last column should be Air Temp
    assert abs(val - 25.1234567) < 0.000001


# ──────────────────── updateCol — ID-based lookup ────────────────────────────


def test_update_col_by_id_succeeds(client):
    """updateCol with a valid id updates the column metadata."""
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    dt = _make_data_type(client)
    u = _make_data_unit(client, dt)

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{a_id}",
        headers=_session_headers(sid),
        json={"name": "a", "datatype": dt, "data_unit": u},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert body["columns"][0]["id"] == a_id


def test_update_col_with_wrong_id_returns_404(client):
    """updateCol with an id that doesn't belong to this scenario returns 404."""
    sid, pid, scn = _make_project(client)
    _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/999999",
        headers=_session_headers(sid),
        json={"name": "a"},
    )
    assert r.status_code == 404


def test_update_col_renames_column_via_id(client):
    """updateCol with id + new name renames the column."""
    sid, pid, scn = _make_project(client)
    a_id, _ = _seed_two_cell_column(client, sid, pid, scn)

    r = client.patch(
        f"{_url(pid, scn, 'updateCol')}/{a_id}",
        headers=_session_headers(sid),
        json={"name": "Renamed_Column"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["columns"][0]["name"] == "Renamed_Column"


# ─────────────────────────── Julian (DOY) datetime upload ────────────────────
#
# Migration 015 catalogued 'YYYY DOY HH:MM' and 'DOY YYYY HH:MM'. The parser
# in _transform_csv recognizes either layout in a single whitespace-split cell
# and normalizes to canonical date + time columns.


def test_upload_julian_year_first_csv(client):
    """'YYYY DOY HH:MM' layout normalizes to canonical date+time."""
    sid, pid, scn = _make_project(client)
    # 2026 is not a leap year — DOY 142 = May 22.
    csv_content = b"Julian,Air Temp\n2026 142 14:30,25.5\n2026 143 15:00,26.0\n"
    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("julian.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 200, r.text

    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    rows = r.json()["rows"]
    dates = sorted(row["date"] for row in rows)
    assert dates == ["2026-05-22", "2026-05-23"]


def test_upload_julian_doy_first_csv(client):
    """'DOY YYYY HH:MM' layout parses to the same calendar date."""
    sid, pid, scn = _make_project(client)
    csv_content = b"DOY,Air Temp\n142 2026 14:30,25.5\n"
    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("julian.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 200, r.text

    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    rows = r.json()["rows"]
    assert rows[0]["date"] == "2026-05-22"
    assert rows[0]["time"] == "14:30:00"


def test_upload_julian_leap_year_doy_366(client):
    """DOY 366 is valid in a leap year (2024 → 2024-12-31)."""
    sid, pid, scn = _make_project(client)
    csv_content = b"Julian,Air Temp\n2024 366 12:00,20.0\n"
    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("julian.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 200, r.text

    r = client.get(
        _url(pid, scn, "getAllTimeSeriesData"),
        headers=_session_headers(sid),
    )
    assert r.json()["rows"][0]["date"] == "2024-12-31"


def test_upload_julian_doy_366_non_leap_returns_400(client):
    """DOY 366 in a non-leap year (2025) is rejected."""
    sid, pid, scn = _make_project(client)
    csv_content = b"Julian,Air Temp\n2025 366 12:00,20.0\n"
    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("julian.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 400


def test_upload_julian_invalid_doy_zero_returns_400(client):
    """DOY must be >= 1; 0 is rejected."""
    sid, pid, scn = _make_project(client)
    csv_content = b"Julian,Air Temp\n2026 0 12:00,20.0\n"
    r = client.post(
        _url(pid, scn, "uploadfile"),
        headers=_session_headers(sid),
        files={"file": ("julian.csv", csv_content, "text/csv")},
    )
    assert r.status_code == 400
