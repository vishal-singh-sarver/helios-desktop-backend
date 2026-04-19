"""
Tests for the weather endpoints + the auto-transform layer.

Covers:
- Unit tests for _transform_csv (CIMIS, ISO 8601, semicolon, AM/PM, fallbacks, rejections)
- Integration tests via TestClient for upload, update, inspect
- Auth / scope tests (wrong session, missing project, missing headers)
"""
from pathlib import Path
from uuid import uuid4

import pytest

from app.core.config import settings
from app.services.weather_service import _transform_csv


# ─────────────────────────── Helpers ─────────────────────────────────────────


def _make_project(client) -> tuple[str, str]:
    """Create a project. Returns (session_id, project_id)."""
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
    return session_id, r.json()["project_id"]


def _csv_on_disk(project_id: str) -> Path:
    """Path the upload endpoint writes to."""
    return settings.data_dir / project_id / "weather.csv"


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

    # Text columns dropped, qc columns dropped, headers slugified
    assert header == ["date", "time", "stn_id", "eto_mm", "air_temp_c", "rel_hum"]
    assert "stn_name" not in header
    assert "cimis_region" not in header
    assert "qc" not in header

    # Date + HHMM normalized
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
    """PyHelios returns NaN for duplicate timestamps. The transformer must
    deduplicate, keeping the last row for each (date, time) pair."""
    raw = (
        b"date,time,temp\n"
        b"2023-07-13,10:00:00,22.5\n"
        b"2023-07-13,10:00:00,99.9\n"
        b"2023-07-13,11:00:00,23.8\n"
    )
    header, rows = _transform_csv(raw)
    assert len(rows) == 2  # duplicate removed
    # The LAST occurrence (99.9) should be kept, not the first (22.5)
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


def test_upload_clean_csv_returns_success_and_writes_file(client):
    session_id, project_id = _make_project(client)

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 200
    body = r.json()
    assert body == {"success": True, "row_count": 3, "column_count": 4}

    # File on disk should match
    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0] == "date,time,temperature,humidity"
    assert saved[1] == "2023-07-13,10:00:00,22.5,65"


def test_upload_cimis_csv_gets_transformed(client):
    session_id, project_id = _make_project(client)

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("cimis.csv", CIMIS_CSV, "text/csv")},
    )

    assert r.status_code == 200
    assert r.json()["row_count"] == 3
    # CIMIS output: date, time, stn_id, eto_mm, air_temp_c, rel_hum
    assert r.json()["column_count"] == 6

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0] == "date,time,stn_id,eto_mm,air_temp_c,rel_hum"
    # 2400 rollover landed correctly
    assert saved[3].startswith("2023-07-14,00:00:00")


def test_upload_empty_file_returns_400(client):
    session_id, project_id = _make_project(client)

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("empty.csv", b"", "text/csv")},
    )

    assert r.status_code == 400
    assert "empty" in r.text.lower()


def test_upload_invalid_csv_format_returns_400(client):
    session_id, project_id = _make_project(client)

    bad = b"name,value\nfoo,1\nbar,2\n"
    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("bad.csv", bad, "text/csv")},
    )

    assert r.status_code == 400


def test_upload_missing_session_id_header_returns_400(client):
    _, project_id = _make_project(client)

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 400
    assert "session_id" in r.text.lower()


def test_upload_unknown_project_returns_404(client):
    session_id = f"session_{uuid4().hex[:8]}"
    bogus_project = uuid4().hex

    r = client.post(
        f"/api/weather/{bogus_project}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_upload_with_wrong_session_returns_404(client):
    """A different user's session can't upload to a project they don't own."""
    owner_session, project_id = _make_project(client)
    intruder_session = f"session_{uuid4().hex[:8]}"
    assert intruder_session != owner_session

    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": intruder_session},
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )

    assert r.status_code == 404


# ─────────────────────────── Add endpoint ────────────────────────────────────


def _upload_clean(client, session_id, project_id):
    r = client.post(
        f"/api/weather/{project_id}/uploadfile",
        headers={"session-id": session_id},
        files={"file": ("test.csv", CLEAN_CSV, "text/csv")},
    )
    assert r.status_code == 200


def test_add_single_row_appends_to_csv(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [{
                "date": "2023-07-13",
                "time": "13:00:00",
                "temperature": "26.0",
                "humidity": "55",
            }]
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["row_count"] == 4  # was 3, now 4
    assert body["column_count"] == 4

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[-1] == "2023-07-13,13:00:00,26.0,55"


def test_add_multiple_rows_in_one_call(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [
                {"date": "2023-07-13", "time": "13:00:00", "temperature": "26.0"},
                {"date": "2023-07-13", "time": "14:00:00", "temperature": "27.0"},
                {"date": "2023-07-13", "time": "15:00:00", "temperature": "28.0"},
            ]
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 6  # was 3, now 6
    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert "13:00:00" in saved[-3]
    assert "14:00:00" in saved[-2]
    assert "15:00:00" in saved[-1]


def test_add_row_with_missing_keys_fills_empty_cells(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    # Only date+time+temperature; humidity should land empty
    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [{
                "date": "2023-07-13",
                "time": "13:00:00",
                "temperature": "26.0",
            }]
        },
    )

    assert r.status_code == 200
    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[-1] == "2023-07-13,13:00:00,26.0,"


def test_add_column_only_appends_and_backfills(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "column": {
                "columnname": "pressure",
                "values": ["1013", "1014"],  # only 2 values for 3 rows
            }
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["row_count"] == 3
    assert body["column_count"] == 5  # was 4, now 5
    assert body["added_column"] == "pressure"

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0] == "date,time,temperature,humidity,pressure"
    assert saved[1].endswith(",1013")
    assert saved[2].endswith(",1014")
    assert saved[3].endswith(",")  # third row gets empty cell


def test_add_column_slugifies_columnname(client):
    """'Pressure (kPa)' should become 'pressure_kpa' to match upload behavior."""
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={"column": {"columnname": "Pressure (kPa)", "values": []}},
    )

    assert r.status_code == 200
    assert r.json()["added_column"] == "pressure_kpa"
    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0].endswith(",pressure_kpa")


def test_add_both_rows_and_column_in_one_call(client):
    """Column added first; new row includes a value for the new column."""
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "column": {"columnname": "pressure", "values": ["1013", "1014", "1015"]},
            "rows": [{
                "date": "2023-07-13",
                "time": "13:00:00",
                "temperature": "26.0",
                "humidity": "55",
                "pressure": "1016",
            }],
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 4
    assert body["column_count"] == 5
    assert body["added_column"] == "pressure"

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0] == "date,time,temperature,humidity,pressure"
    assert saved[-1] == "2023-07-13,13:00:00,26.0,55,1016"


def test_add_empty_body_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={},
    )

    assert r.status_code == 400
    assert "rows" in r.text.lower()
    assert "column" in r.text.lower()


def test_add_duplicate_row_against_existing_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [{
                "date": "2023-07-13",
                "time": "10:00:00",  # already exists
                "temperature": "99",
            }]
        },
    )

    assert r.status_code == 400
    assert "already exists" in r.text.lower()


def test_add_duplicate_rows_within_batch_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [
                {"date": "2023-07-13", "time": "13:00:00", "temperature": "26"},
                {"date": "2023-07-13", "time": "13:00:00", "temperature": "27"},
            ]
        },
    )

    assert r.status_code == 400
    assert "duplicate" in r.text.lower()


def test_add_existing_column_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={"column": {"columnname": "temperature", "values": []}},
    )

    assert r.status_code == 400
    assert "already exists" in r.text.lower()


def test_add_column_named_date_or_time_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    for name in ("date", "time", "Date", "TIME"):
        r = client.post(
            f"/api/weather/{project_id}/add",
            headers={"session-id": session_id},
            json={"column": {"columnname": name, "values": []}},
        )
        assert r.status_code == 400, f"expected 400 for columnname={name!r}"


def test_add_invalid_column_name_returns_400(client):
    """Pure-symbol name slugifies to empty → 400."""
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={"column": {"columnname": "$%&!", "values": []}},
    )

    assert r.status_code == 400
    assert "invalid" in r.text.lower()


def test_add_non_numeric_row_value_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={
            "rows": [{
                "date": "2023-07-13",
                "time": "13:00:00",
                "temperature": "hot",
            }]
        },
    )

    assert r.status_code == 400
    assert "not numeric" in r.text.lower()


def test_add_non_numeric_column_value_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={"column": {"columnname": "pressure", "values": ["1013", "bad"]}},
    )

    assert r.status_code == 400
    assert "not numeric" in r.text.lower()


def test_add_row_missing_date_or_time_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": session_id},
        json={"rows": [{"temperature": "20"}]},
    )

    assert r.status_code == 400
    assert "date" in r.text.lower()


def test_add_with_wrong_session_returns_404(client):
    owner_session, project_id = _make_project(client)
    _upload_clean(client, owner_session, project_id)

    intruder = f"session_{uuid4().hex[:8]}"
    r = client.post(
        f"/api/weather/{project_id}/add",
        headers={"session-id": intruder},
        json={"rows": [{"date": "2023-07-13", "time": "13:00:00", "temperature": "20"}]},
    )

    assert r.status_code == 404


def test_add_unknown_project_returns_404(client):
    session_id = f"session_{uuid4().hex[:8]}"
    bogus = uuid4().hex
    r = client.post(
        f"/api/weather/{bogus}/add",
        headers={"session-id": session_id},
        json={"column": {"columnname": "pressure", "values": []}},
    )
    assert r.status_code == 404


# ─────────────────────────── Update endpoint ─────────────────────────────────


def test_update_cell_writes_new_value_to_disk(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "temperature",
            "value": "99.9",
        },
    )

    assert r.status_code == 200
    assert r.json() == {"success": True}

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert "99.9" in saved[1]


def test_update_clears_cell_with_empty_value(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "11:00:00"},
            "col": "humidity",
            "value": "",
        },
    )

    assert r.status_code == 200
    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[2].endswith(",")


def test_update_non_numeric_value_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "temperature",
            "value": "hot",
        },
    )

    assert r.status_code == 400
    assert "not numeric" in r.text.lower()


def test_update_unknown_column_returns_404(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "pressure",
            "value": "1013",
        },
    )

    assert r.status_code == 404
    assert "column" in r.text.lower()


def test_update_unknown_row_returns_404(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2024-01-01", "time": "00:00:00"},
            "col": "temperature",
            "value": "10.0",
        },
    )

    assert r.status_code == 404
    assert "no row" in r.text.lower()


def test_update_date_or_time_column_returns_400(client):
    """Cannot update the date or time column (would break PyHelios key)."""
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "date",
            "value": "2024-01-01",
        },
    )
    assert r.status_code == 400


def test_update_with_wrong_session_returns_404(client):
    owner_session, project_id = _make_project(client)
    _upload_clean(client, owner_session, project_id)

    intruder = f"session_{uuid4().hex[:8]}"
    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": intruder},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "temperature",
            "value": "1.0",
        },
    )

    assert r.status_code == 404


def test_update_prefers_updateTabularTimeseriesData_when_available(
    client, monkeypatch
):
    """When PyHelios adds updateTabularTimeseriesData in a future release,
    our sync helper must automatically prefer it over the clear+reload
    fallback. Mock the method onto the live Context and verify."""
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    from app.core.session_store import registry
    pctx = registry.get_context(session_id, project_id)
    assert pctx is not None

    # Skip this assertion if PyHelios isn't loaded in the current run
    if pctx.context is None:
        import pytest
        pytest.skip("PyHelios Context not loaded")

    preferred_called = {"count": 0}
    fallback_called = {"count": 0}

    real_clear = pctx.context.clearTimeseriesData
    real_load = pctx.context.loadTabularTimeseriesData

    def fake_update(path, labels, delim, fmt, headerlines):
        preferred_called["count"] += 1
        # Also re-load for real so subsequent queries still work
        real_clear()
        real_load(path, labels, delim, fmt, headerlines)

    def wrapped_load(path, labels, delim, fmt, headerlines):
        fallback_called["count"] += 1
        real_load(path, labels, delim, fmt, headerlines)

    # Attach the future method to the live context
    monkeypatch.setattr(
        pctx.context, "updateTabularTimeseriesData", fake_update, raising=False
    )
    monkeypatch.setattr(
        pctx.context, "loadTabularTimeseriesData", wrapped_load
    )

    r = client.post(
        f"/api/weather/{project_id}/update",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "col": "temperature",
            "value": "42.0",
        },
    )
    assert r.status_code == 200
    # The future method should have been preferred; fallback load should NOT
    # have fired (wrapped_load only counts explicit fallback calls)
    assert preferred_called["count"] == 1
    assert fallback_called["count"] == 0


# ─────────────────────────── Delete endpoint ─────────────────────────────────


def test_delete_row_removes_it_from_csv(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={"row": {"date": "2023-07-13", "time": "10:00:00"}},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["row_count"] == 2  # was 3
    assert body["column_count"] == 4

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    # Header + 2 remaining rows
    assert len(saved) == 3
    # The deleted row should not appear
    assert not any("10:00:00" in line for line in saved)


def test_delete_column_removes_it_from_header_and_rows(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={"column": {"columnname": "humidity"}},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 3
    assert body["column_count"] == 3  # was 4

    saved = _csv_on_disk(project_id).read_text(encoding="utf-8").splitlines()
    assert saved[0] == "date,time,temperature"
    # No humidity values in any row
    assert all(len(line.split(",")) == 3 for line in saved)


def test_delete_both_row_and_column(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={
            "row": {"date": "2023-07-13", "time": "10:00:00"},
            "column": {"columnname": "humidity"},
        },
    )

    assert r.status_code == 200
    body = r.json()
    assert body["row_count"] == 2
    assert body["column_count"] == 3


def test_delete_entire_file_when_body_is_empty(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    # File exists before delete
    assert _csv_on_disk(project_id).exists()

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["row_count"] == 0
    assert body["column_count"] == 0

    # File should be gone from disk
    assert not _csv_on_disk(project_id).exists()


def test_delete_unknown_row_returns_404(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={"row": {"date": "2099-01-01", "time": "00:00:00"}},
    )

    assert r.status_code == 404
    assert "no row" in r.text.lower()


def test_delete_unknown_column_returns_404(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": session_id},
        json={"column": {"columnname": "nonexistent"}},
    )

    assert r.status_code == 404
    assert "not found" in r.text.lower()


def test_delete_date_or_time_column_returns_400(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    for name in ("date", "time", "Date", "TIME"):
        r = client.post(
            f"/api/weather/{project_id}/delete",
            headers={"session-id": session_id},
            json={"column": {"columnname": name}},
        )
        assert r.status_code == 400, f"expected 400 for columnname={name!r}"
        assert "cannot delete" in r.text.lower()


def test_delete_with_wrong_session_returns_404(client):
    owner_session, project_id = _make_project(client)
    _upload_clean(client, owner_session, project_id)

    intruder = f"session_{uuid4().hex[:8]}"
    r = client.post(
        f"/api/weather/{project_id}/delete",
        headers={"session-id": intruder},
        json={"row": {"date": "2023-07-13", "time": "10:00:00"}},
    )

    assert r.status_code == 404


def test_delete_unknown_project_returns_404(client):
    session_id = f"session_{uuid4().hex[:8]}"
    bogus = uuid4().hex
    r = client.post(
        f"/api/weather/{bogus}/delete",
        headers={"session-id": session_id},
        json={"column": {"columnname": "temperature"}},
    )
    assert r.status_code == 404


# ─────────────────────────── Inspect endpoint ────────────────────────────────


def test_inspect_after_upload_shows_file_state(client):
    session_id, project_id = _make_project(client)
    _upload_clean(client, session_id, project_id)

    r = client.get(
        f"/api/weather/{project_id}/inspect",
        headers={"session-id": session_id},
    )

    assert r.status_code == 200
    body = r.json()
    assert body["file"]["exists"] is True
    assert body["file"]["row_count"] == 3
    assert body["file"]["column_count"] == 4
    assert body["file"]["header"] == ["date", "time", "temperature", "humidity"]
    assert body["file"]["first_rows"][0] == [
        "2023-07-13", "10:00:00", "22.5", "65",
    ]


def test_inspect_with_no_upload_shows_no_file(client):
    session_id, project_id = _make_project(client)

    r = client.get(
        f"/api/weather/{project_id}/inspect",
        headers={"session-id": session_id},
    )

    assert r.status_code == 200
    assert r.json()["file"]["exists"] is False


def test_inspect_unknown_project_returns_404(client):
    session_id = f"session_{uuid4().hex[:8]}"
    bogus = uuid4().hex
    r = client.get(
        f"/api/weather/{bogus}/inspect",
        headers={"session-id": session_id},
    )
    assert r.status_code == 404
