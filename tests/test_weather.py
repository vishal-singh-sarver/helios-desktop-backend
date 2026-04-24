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
