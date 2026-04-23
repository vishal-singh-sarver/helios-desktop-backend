"""
Weather data service — per-scenario.

Each scenario in a project owns its own weather CSV:
    backend-api/data/<project_id>/<scenario_id>/weather.csv

Uploaded CSVs go through an auto-transform step that detects date/time
columns, normalizes formats, and drops non-numeric columns — so users
can upload files in any reasonable format (CIMIS, NOAA, custom, etc.).
The cleaned CSV is saved and PyHelios is told to load it via:

    ctx.clearTimeseriesData()
    ctx.loadTabularTimeseriesData(path, column_labels, ",", "YYYY-MM-DD", 1)

PyHelios's loadTabularTimeseriesData takes a path string, not a file
object — it opens the file itself. That's why we always write to disk
before calling it.
"""
from __future__ import annotations

import csv
import io
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import HTTPException

from app.core.config import settings
from app.db.models import Scenario
from app.helios import context as helios_ctx

if TYPE_CHECKING:
    from sqlalchemy.orm import Session
    from app.core.scenario_context import ScenarioContext


# ─── Path + file I/O helpers ─────────────────────────────────────────────────


def _csv_path(project_id: str, scenario_id: str) -> Path:
    """Return the on-disk path for this scenario's weather CSV.
    Creates the scenario directory under backend-api/data if missing."""
    scn_dir = settings.data_dir / project_id / scenario_id
    scn_dir.mkdir(parents=True, exist_ok=True)
    return scn_dir / "weather.csv"


def _persist_weather_path(
    db: "Session", project_id: str, scenario_id: str, path: Path | None
) -> None:
    """Keep the scenario's weather_file_path column in sync with disk state."""
    scenario = (
        db.query(Scenario)
        .filter(Scenario.id == scenario_id, Scenario.project_id == project_id)
        .first()
    )
    if scenario is None:
        return
    new_value = str(path) if path else None
    if scenario.weather_file_path != new_value:
        scenario.weather_file_path = new_value
        try:
            db.commit()
        except Exception:
            db.rollback()


def _write_csv(path: Path, header: list[str], rows: list[list[str]]) -> None:
    """Write the CSV back to disk. Closes the file before returning so
    PyHelios can immediately reopen it (important on Windows)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)


def _reload_pyhelios(sctx: "ScenarioContext", path: Path, header: list[str]) -> None:
    """Tell PyHelios to clear and reload its timeseries from the CSV file.
    No-op if PyHelios isn't available or the scenario has no live Context."""
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        return
    try:
        sctx.context.clearTimeseriesData()
        sctx.context.loadTabularTimeseriesData(
            str(path),
            list(header),
            ",",
            "YYYY-MM-DD",
            1,
        )
    except Exception as exc:
        raise HTTPException(503, f"PyHelios reload failed: {exc}")


# ─── Auto-transform: any-format CSV → standard (date, time, numeric...) ──────


_DATE_FORMATS = [
    "%Y-%m-%d",    # 2023-07-13
    "%m/%d/%Y",    # 7/13/2023   (US-style — tried before D/M to favor CIMIS)
    "%d/%m/%Y",    # 13/7/2023
    "%Y/%m/%d",    # 2023/7/13
    "%d-%m-%Y",    # 13-07-2023
    "%m-%d-%Y",    # 07-13-2023
    "%Y%m%d",      # 20230713
    "%d-%b-%Y",    # 13-Jul-2023
    "%d %b %Y",    # 13 Jul 2023
    "%b %d %Y",    # Jul 13 2023
    "%b %d, %Y",   # Jul 13, 2023
    "%d %B %Y",    # 13 July 2023
    "%B %d %Y",    # July 13 2023
    "%B %d, %Y",   # July 13, 2023
    "%Y-%b-%d",    # 2023-Jul-13
]

_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",        # 2023-07-13T10:00:00Z (ISO 8601 UTC)
    "%Y-%m-%dT%H:%M:%S",         # 2023-07-13T10:00:00
    "%Y-%m-%dT%H:%M",            # 2023-07-13T10:00
    "%Y-%m-%d %H:%M:%S",         # 2023-07-13 10:00:00
    "%Y-%m-%d %H:%M",            # 2023-07-13 10:00
    "%m/%d/%Y %H:%M:%S",         # 7/13/2023 10:00:00
    "%m/%d/%Y %H:%M",            # 7/13/2023 10:00
    "%m/%d/%Y %I:%M:%S %p",      # 7/13/2023 10:00:00 AM
    "%m/%d/%Y %I:%M %p",         # 7/13/2023 10:00 AM
    "%d/%m/%Y %H:%M:%S",         # 13/7/2023 10:00:00
    "%d/%m/%Y %H:%M",            # 13/7/2023 10:00
    "%Y%m%d%H%M%S",              # 20230713100000
    "%Y%m%d%H%M",                # 202307131000 (Ameriflux TIMESTAMP_START)
]


def _slugify(name: str) -> str:
    """'Air Temp (C)' → 'air_temp_c'. Empty/all-symbol input → ''."""
    chars = [c.lower() if c.isalnum() else "_" for c in name.strip()]
    return "_".join(p for p in "".join(chars).split("_") if p)


def _parse_date(s: str) -> datetime:
    s = s.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"could not parse date '{s}'")


def _parse_datetime(s: str) -> tuple[datetime, bool]:
    """Parse a combined date+time string. Returns (dt, rollover). rollover is
    True for the '24:00:00' end-of-day convention."""
    s = s.strip().replace("  ", " ")
    rollover = False
    # Handle the '24:00:00' end-of-day convention by swapping to '00:00:00'
    # and flagging rollover.
    if " 24:00" in s or "T24:00" in s:
        s = s.replace(" 24:00", " 00:00").replace("T24:00", "T00:00")
        rollover = True
    for fmt in _DATETIME_FORMATS:
        try:
            return datetime.strptime(s, fmt), rollover
        except ValueError:
            continue
    raise ValueError(f"could not parse datetime '{s}'")


def _parse_time(s: str) -> tuple[int, int, int, bool]:
    """Parse a time string. Returns (hour, minute, second, rollover_to_next_day).
    Handles HHMM, HHMMSS, HH:MM, HH:MM:SS, 12-hour AM/PM. '2400' → rollover."""
    s = s.strip()
    # HHMM (4 digits) and HHMMSS (6 digits)
    if s.isdigit():
        if len(s) == 4:
            h, m, sec = int(s[:2]), int(s[2:]), 0
        elif len(s) == 6:
            h, m, sec = int(s[:2]), int(s[2:4]), int(s[4:])
        else:
            raise ValueError(f"could not parse time '{s}'")
        if h == 24 and m == 0 and sec == 0:
            return 0, 0, 0, True
        if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59:
            return h, m, sec, False
        raise ValueError(f"time '{s}' out of range")
    # 12-hour with AM/PM
    lowered = s.lower()
    if lowered.endswith(("am", "pm")) or " am" in lowered or " pm" in lowered:
        for fmt in ("%I:%M:%S %p", "%I:%M %p", "%I %p"):
            try:
                t = datetime.strptime(s.upper(), fmt)
                return t.hour, t.minute, t.second, False
            except ValueError:
                continue
    # HH:MM or HH:MM:SS (24-hour)
    if ":" in s:
        parts = s.split(":")
        if 2 <= len(parts) <= 3:
            try:
                h, m = int(parts[0]), int(parts[1])
                # Strip fractional seconds if present (e.g. "10.500")
                sec_str = parts[2].split(".")[0] if len(parts) == 3 else "0"
                sec = int(sec_str)
                if h == 24 and m == 0 and sec == 0:
                    return 0, 0, 0, True
                if 0 <= h <= 23 and 0 <= m <= 59 and 0 <= sec <= 59:
                    return h, m, sec, False
            except ValueError:
                pass
    raise ValueError(f"could not parse time '{s}'")


def _is_numeric_or_empty(s: str) -> bool:
    s = s.strip()
    if not s:
        return True
    try:
        float(s)
        return True
    except ValueError:
        return False


def _sniff_delimiter(text: str) -> str:
    """Auto-detect CSV delimiter. Falls back to ',' on failure."""
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _looks_like_datetime(values: list[str]) -> bool:
    """Return True if at least one non-empty value parses as a combined datetime."""
    for v in values[:10]:
        if v.strip():
            try:
                _parse_datetime(v)
                return True
            except ValueError:
                return False
    return False


def _looks_like_date(values: list[str]) -> bool:
    for v in values[:10]:
        if v.strip():
            try:
                _parse_date(v)
                return True
            except ValueError:
                return False
    return False


def _looks_like_time(values: list[str]) -> bool:
    for v in values[:10]:
        if v.strip():
            try:
                _parse_time(v)
                return True
            except ValueError:
                return False
    return False


def _find_datetime_column(
    header: list[str], data_rows: list[list[str]]
) -> int | None:
    """Find a single column that combines date and time. Prefers header-name
    match, falls back to value-shape detection."""
    # Header-name match
    for i, h in enumerate(header):
        lh = h.lower()
        if "datetime" in lh or "timestamp" in lh or "date_time" in lh:
            return i
    # Value-shape fallback: scan columns, pick first that parses as datetime
    for i in range(len(header)):
        col_values = [r[i] for r in data_rows if i < len(r)]
        if _looks_like_datetime(col_values):
            return i
    return None


def _find_date_column(header: list[str], data_rows: list[list[str]]) -> int | None:
    for i, h in enumerate(header):
        if "date" in h.lower():
            return i
    for i in range(len(header)):
        col_values = [r[i] for r in data_rows if i < len(r)]
        if _looks_like_date(col_values):
            return i
    return None


def _find_time_column(
    header: list[str], data_rows: list[list[str]], skip: set[int]
) -> int | None:
    for i, h in enumerate(header):
        if i in skip:
            continue
        lh = h.lower()
        if "time" in lh or "hour" in lh:
            return i
    for i in range(len(header)):
        if i in skip:
            continue
        col_values = [r[i] for r in data_rows if i < len(r)]
        if _looks_like_time(col_values):
            return i
    return None


def _transform_csv(raw_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Take raw CSV bytes in any reasonable format, return (header, rows) in
    our standard: first column 'date' (YYYY-MM-DD), second 'time' (HH:MM:SS),
    then numeric data columns with slugified names. Drops text/qc-flag cols.

    Handles: comma/semicolon/tab delimiters, combined datetime columns (ISO
    8601, NOAA, etc.), split date+time columns, many date/time formats,
    12-hour AM/PM, end-of-day '2400' → next day rollover."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    delimiter = _sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        raise HTTPException(400, "CSV needs a header row plus at least one data row")

    raw_header = [c.strip() for c in rows[0]]
    data_rows = rows[1:]

    # Try combined datetime column first. If we find one, we use it for both
    # date and time. Otherwise, fall back to finding separate columns.
    datetime_idx = _find_datetime_column(raw_header, data_rows)
    if datetime_idx is not None:
        date_idx = time_idx = datetime_idx
        skip_for_data = {datetime_idx}
    else:
        date_idx = _find_date_column(raw_header, data_rows)
        if date_idx is None:
            raise HTTPException(
                400,
                "could not find a date column (need a header containing 'date', "
                "or a column with date-shaped values)",
            )
        time_idx = _find_time_column(raw_header, data_rows, skip={date_idx})
        if time_idx is None:
            raise HTTPException(
                400,
                "could not find a time column (need a header containing 'time' "
                "or 'hour', or a column with time-shaped values)",
            )
        skip_for_data = {date_idx, time_idx}

    # Pick numeric data columns. Skip date/time, blank/duplicate slugs, and
    # anything that has a non-numeric non-empty value (text columns, qc flags).
    data_cols: list[tuple[int, str]] = []
    seen = {"date", "time"}
    for i, h in enumerate(raw_header):
        if i in skip_for_data:
            continue
        slug = _slugify(h)
        if not slug or slug in seen:
            continue
        all_numeric = all(
            _is_numeric_or_empty(r[i]) if i < len(r) else True for r in data_rows
        )
        has_value = any(r[i].strip() for r in data_rows if i < len(r))
        if all_numeric and has_value:
            data_cols.append((i, slug))
            seen.add(slug)

    if not data_cols:
        raise HTTPException(400, "no numeric data columns found")

    new_header = ["date", "time"] + [s for _, s in data_cols]
    new_rows: list[list[str]] = []
    for r in data_rows:
        max_idx = max([date_idx, time_idx] + [i for i, _ in data_cols])
        if max_idx >= len(r):
            continue
        try:
            if datetime_idx is not None:
                dt, rollover = _parse_datetime(r[datetime_idx])
                d = dt
                h, m, sec = dt.hour, dt.minute, dt.second
            else:
                d = _parse_date(r[date_idx])
                h, m, sec, rollover = _parse_time(r[time_idx])
        except ValueError as exc:
            raise HTTPException(400, str(exc))
        if rollover:
            d = d + timedelta(days=1)
        new_row = [
            d.strftime("%Y-%m-%d"),
            f"{h:02d}:{m:02d}:{sec:02d}",
        ]
        for i, _ in data_cols:
            new_row.append(r[i].strip() if i < len(r) else "")
        new_rows.append(new_row)

    # Deduplicate by (date, time). PyHelios returns NaN for duplicate
    # timestamps instead of erroring, so we must prevent them.
    # Keep the LAST row for each (date, time) pair.
    seen: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(new_rows):
        key = (row[0], row[1])
        seen[key] = idx
    if len(seen) < len(new_rows):
        new_rows = [new_rows[i] for i in sorted(seen.values())]

    return new_header, new_rows


# ─── Endpoint handler ────────────────────────────────────────────────────────


def upload_file(sctx: "ScenarioContext", file_bytes: bytes, db: "Session") -> dict:
    """Auto-transform uploaded CSV, save to disk, reload PyHelios, and
    persist the scenario's weather_file_path column."""
    if not file_bytes:
        raise HTTPException(400, "uploaded file is empty")

    header, rows = _transform_csv(file_bytes)

    path = _csv_path(sctx.project_id, sctx.scenario_id)
    _write_csv(path, header, rows)

    _reload_pyhelios(sctx, path, header)
    _persist_weather_path(db, sctx.project_id, sctx.scenario_id, path)

    return {
        "success": True,
        "row_count": len(rows),
        "column_count": len(header),
    }

