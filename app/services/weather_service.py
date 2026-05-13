"""
Weather data service — per-scenario, session-only state.

All weather data lives in PyHelios memory (no permanent file on disk).
Uploads are normalized in Python, streamed through a temp file, and
bulk-loaded into PyHelios via loadTabularTimeseriesData. Surgical
add/update/delete endpoints mutate cells directly via addTimeseriesData
and updateTimeseriesData.

PyHelios timeseries methods used (v0.1.19):
    addTimeseriesData(label, value, Date, Time)         add ONE cell
    updateTimeseriesData(label, Date, Time, new_value)  update ONE cell (must exist)
    clearTimeseriesData()                               wipe everything
    loadTabularTimeseriesData(path, labels, delim,
                              date_format, headerlines) bulk load
    listTimeseriesVariables()                           list labels
    getTimeseriesLength(label)                          row count for a label
    queryTimeseriesData / Date / Time (label, index)    read
"""
from __future__ import annotations

import csv
import io
import math
import os
import tempfile
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.db.models import DataUnit, HeliosDataType, WeatherDataHeader
from app.helios import context as helios_ctx
from app.helios.persistence import trigger_autosave

# PyHelios's specific exception for "thing not found" errors (missing label,
# missing cell, etc.). Defensive import: if PyHelios isn't available, define
# a dummy so this module can still load.
try:
    from pyhelios.exceptions import HeliosRuntimeError
except ImportError:  # pragma: no cover
    class HeliosRuntimeError(Exception):
        pass

if TYPE_CHECKING:
    from app.core.scenario_context import ScenarioContext
    from app.schemas.weather import AddColumn, DeleteRequest, UpdateRequest


# ─── Shared helpers ──────────────────────────────────────────────────────────


def _helios_date_time(date_str: str, time_str: str):
    """Parse 'YYYY-MM-DD' + 'HH:MM[:SS]' into PyHelios (Date, Time) objects.

    Time accepts both `HH:MM` (browser <input type="time">, CIMIS, partial
    ISO) and `HH:MM:SS`. Missing seconds default to 0.

    Range validation is delegated to PyHelios's Date/Time constructors —
    we don't duplicate it. The try/except covers both string-format errors
    (int parse fails / wrong arity) and PyHelios's range errors (Date/Time
    __init__ raises), so any bad input → clean 400 instead of leaking as 500.
    """
    try:
        y, mo, d = (int(p) for p in date_str.split("-"))
        time_parts = time_str.split(":")
        if len(time_parts) == 2:
            time_parts = (*time_parts, "0")
        hh, mm, ss = (int(p) for p in time_parts)
        return helios_ctx.Date(y, mo, d), helios_ctx.Time(hh, mm, ss)
    except (ValueError, AttributeError) as exc:
        raise HTTPException(400, f"bad date/time '{date_str}' '{time_str}': {exc}")


def _label_has_timestamp(ctx, label: str, date_obj, time_obj) -> bool:
    """True if this label has a cell at exactly (date, time). O(n) per call."""
    n = ctx.getTimeseriesLength(label)
    for i in range(n):
        d = ctx.queryTimeseriesDate(label, i)
        t = ctx.queryTimeseriesTime(label, i)
        if d == date_obj and t == time_obj:
            return True
    return False


def _clean_float(v: float) -> float | None:
    """NaN → None so JSON serialization doesn't choke. Otherwise float rounded to max 7 decimals."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return round(float(v), 7)


def _is_numeric_value(v: Any) -> bool:
    """True if v is int/float (not bool/NaN) or a numeric string."""
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return not math.isnan(v)
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return False
        try:
            float(s)
            return True
        except ValueError:
            return False
    return False


def _has_excess_decimals(v: Any) -> bool:
    """True if numeric string or float has more than 7 decimal places."""
    if v is None:
        return False
    vstr = str(v).strip().upper()
    if vstr in ("", "NAN"):
        return False
    if "." in vstr:
        # e.g. "123.45678901" -> "45678901"
        decimal_part = vstr.split(".")[1]
        # Ignore exponent part if present
        if "E" in decimal_part:
            decimal_part = decimal_part.split("E")[0]
        return len(decimal_part) > 7
    return False


def _truncate_decimals(vstr: str) -> str:
    """Truncate to 7 decimal places if needed."""
    if vstr is None:
        return vstr
    vstr = str(vstr).strip()
    if "." in vstr:
        integer_part, decimal_part = vstr.split(".")
        if len(decimal_part) > 7:
            return f"{integer_part}.{decimal_part[:7]}"
    return vstr


# ─── Auto-transform: any-format CSV → standard (date, time, numeric...) ──────


_DATE_FORMATS = [
    "%Y-%m-%d",    # 2023-07-13   (canonical / clean CSV)
    "%m/%d/%Y",    # 7/13/2023    (CIMIS)
]

_DATETIME_FORMATS = [
    "%Y-%m-%dT%H:%M:%SZ",  # 2023-07-13T10:00:00Z (ISO 8601 UTC)
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
    lowered = s.lower()
    if lowered.endswith(("am", "pm")) or " am" in lowered or " pm" in lowered:
        for fmt in ("%I:%M:%S %p", "%I:%M %p", "%I %p"):
            try:
                t = datetime.strptime(s.upper(), fmt)
                return t.hour, t.minute, t.second, False
            except ValueError:
                continue
    if ":" in s:
        parts = s.split(":")
        if 2 <= len(parts) <= 3:
            try:
                h, m = int(parts[0]), int(parts[1])
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
    sample = text[:4096]
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _looks_like_datetime(values: list[str]) -> bool:
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


def _find_datetime_column(header: list[str], data_rows: list[list[str]]) -> int | None:
    for i, h in enumerate(header):
        lh = h.lower()
        if "datetime" in lh or "timestamp" in lh or "date_time" in lh:
            return i
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


def _transform_csv(raw_bytes: bytes) -> tuple[list[str], list[list[str]], bool]:
    """Take raw CSV bytes in any reasonable format, return (header, rows, truncated_flag) in
    our standard: first column 'date' (YYYY-MM-DD), second 'time' (HH:MM:SS),
    then numeric data columns with slugified names. Drops text/qc-flag cols."""
    text = raw_bytes.decode("utf-8-sig", errors="replace")
    delimiter = _sniff_delimiter(text)
    rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    if len(rows) < 2:
        raise HTTPException(400, "CSV needs a header row plus at least one data row")

    raw_header = [c.strip() for c in rows[0]]
    data_rows = rows[1:]

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

    data_cols: list[tuple[int, str]] = []
    seen_slugs = {"date", "time"}
    for i, h in enumerate(raw_header):
        if i in skip_for_data:
            continue
        slug = _slugify(h)
        if not slug or slug in seen_slugs:
            continue
        all_numeric = all(
            _is_numeric_or_empty(r[i]) if i < len(r) else True for r in data_rows
        )
        has_value = any(r[i].strip() for r in data_rows if i < len(r))
        if all_numeric and has_value:
            data_cols.append((i, slug))
            seen_slugs.add(slug)

    if not data_cols:
        raise HTTPException(400, "no numeric data columns found")

    new_header = ["date", "time"] + [s for _, s in data_cols]
    new_rows: list[list[str]] = []
    truncated_any = False
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
            if i < len(r):
                val = r[i].strip()
                if _has_excess_decimals(val):
                    val = _truncate_decimals(val)
                    truncated_any = True
                new_row.append(val)
            else:
                new_row.append("")
        new_rows.append(new_row)

    # Deduplicate by (date, time). Keep the LAST row for each pair — PyHelios
    # silently drops duplicate timestamps with only a stderr warning.
    seen: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(new_rows):
        key = (row[0], row[1])
        seen[key] = idx
    if len(seen) < len(new_rows):
        new_rows = [new_rows[i] for i in sorted(seen.values())]

    return new_header, new_rows, truncated_any


# ─── Read endpoints ──────────────────────────────────────────────────────────


def inspect(sctx: "ScenarioContext") -> dict:
    """Lightweight probe: availability + first 3 rows as a sample."""
    pyhelios_available = helios_ctx.PYHELIOS_AVAILABLE
    pyhelios_state: dict | None = None

    if pyhelios_available and sctx.context is not None:
        ctx = sctx.context
        labels = list(ctx.listTimeseriesVariables())
        row_count = ctx.getTimeseriesLength(labels[0]) if labels else 0

        samples: list[dict] = []
        for i in range(min(3, row_count)):
            date_obj = ctx.queryTimeseriesDate(labels[0], i)
            time_obj = ctx.queryTimeseriesTime(labels[0], i)
            row: dict[str, Any] = {
                "date": str(date_obj),
                "time": str(time_obj),
            }
            # Use index-based query — query-by-date/time interpolates and
            # returns NaN for every point if any cell in the label is NaN.
            for label in labels:
                row[label] = _clean_float(ctx.queryTimeseriesData(label, index=i))
            samples.append(row)

        pyhelios_state = {
            "loaded": True,
            "labels": ["date", "time"] + labels,
            "row_count": row_count,
            "first_rows": samples,
        }

    return {
        "pyhelios_available": pyhelios_available,
        "file": {
            "exists": False,
            "note": "no file — content lives in PyHelios memory only",
        },
        "pyhelios_state": pyhelios_state,
    }


def get_all_timeseries_data(
    sctx: "ScenarioContext",
    limit: int | None = None,
    offset: int = 0,
) -> dict:
    """Full table read, with optional paging via limit/offset."""
    empty = {
        "success": True,
        "labels": ["date", "time"],
        "row_count": 0,
        "total_rows": 0,
        "column_count": 2,
        "offset": 0,
        "limit": None,
        "rows": [],
    }
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        return empty

    ctx = sctx.context
    labels = list(ctx.listTimeseriesVariables())
    if not labels:
        return empty

    anchor = labels[0]
    total = ctx.getTimeseriesLength(anchor)

    start = max(0, offset)
    end = min(total, start + limit) if limit is not None else total
    if start >= total:
        return {
            **empty,
            "total_rows": total,
            "offset": start,
            "limit": limit,
        }

    rows: list[dict[str, Any]] = []
    for i in range(start, end):
        date_obj = ctx.queryTimeseriesDate(anchor, i)
        time_obj = ctx.queryTimeseriesTime(anchor, i)
        row: dict[str, Any] = {
            "date": str(date_obj),
            "time": str(time_obj),
        }
        # Use index-based query — query-by-date/time interpolates and
        # returns NaN for every point if any cell in the label is NaN.
        for label in labels:
            row[label] = _clean_float(ctx.queryTimeseriesData(label, index=i))
        rows.append(row)

    return {
        "success": True,
        "labels": ["date", "time"] + labels,
        "row_count": end - start,
        "total_rows": total,
        "column_count": 2 + len(labels),
        "offset": start,
        "limit": limit,
        "rows": rows,
    }


# ─── upload_file — bulk load via loadTabularTimeseriesData ───────────────────


def _write_temp_csv(header: list[str], rows: list[list[str]]) -> str:
    """Write a CSV to a temp file, return its path. Caller must delete."""
    fd, path = tempfile.mkstemp(suffix=".csv", prefix="helios_weather_")
    os.close(fd)
    try:
        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(header)
            writer.writerows(rows)
    except Exception:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def upload_file(sctx: "ScenarioContext", file_bytes: bytes) -> dict:
    """Transform the uploaded CSV and bulk-load it into PyHelios via a temp file."""
    if not file_bytes:
        raise HTTPException(400, "uploaded file is empty")

    header, rows, truncated_any = _transform_csv(file_bytes)

    if helios_ctx.PYHELIOS_AVAILABLE and sctx.context is not None:
        ctx = sctx.context
        ctx.clearTimeseriesData()

        temp_path = _write_temp_csv(header, rows)
        try:
            ctx.loadTabularTimeseriesData(
                temp_path,
                list(header),
                ",",
                "YYYY-MM-DD",
                1,
            )
        except Exception as exc:
            raise HTTPException(503, f"PyHelios load failed: {exc}")
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    response = {
        "success": True,
        "row_count": len(rows),
        "column_count": len(header),
    }
    if truncated_any:
        response["message"] = "Some values contained more than 7 decimal places and were truncated."
    
    trigger_autosave(sctx.context, sctx.project_id)
    return response


# ─── add — the only place with row/column wrappers ───────────────────────────


def _add_row(ctx, date_str: str, time_str: str, cells: dict[str, float]) -> None:
    """Add ONE row at (date, time). Caller passes only numeric cells."""
    date_obj, time_obj = _helios_date_time(date_str, time_str)
    for label, value in cells.items():
        ctx.addTimeseriesData(label, float(value), date_obj, time_obj)


def _add_column(ctx, label: str, cells: list[tuple[Any, Any, float]]) -> None:
    """Add ONE column. `cells` is pre-aligned (Date, Time, value) tuples."""
    for d, t, value in cells:
        ctx.addTimeseriesData(label, float(value), d, t)


def _scenario_timestamps(ctx, exclude_labels: set[str] | None = None) -> list[tuple[Any, Any]]:
    """Return all (date, time) pairs from PyHelios for this scenario.

    Anchors on the first registered label and reads its cells in order.
    Relies on the invariant that every label in a scenario shares the
    same (date, time) set (maintained by addCol / addRow). Returns an
    empty list if no usable label exists (scenario has no rows).
    """
    candidates = list(ctx.listTimeseriesVariables())
    if exclude_labels:
        candidates = [l for l in candidates if l not in exclude_labels]
    if not candidates:
        return []
    anchor = candidates[0]
    n = ctx.getTimeseriesLength(anchor)
    return [
        (ctx.queryTimeseriesDate(anchor, i), ctx.queryTimeseriesTime(anchor, i))
        for i in range(n)
    ]


def _label_timestamp_set(ctx, label: str) -> set[tuple[str, str]]:
    """Return {(str(date), str(time))} for cells already present under `label`.

    Used to detect which scenario timestamps already have a cell for a
    given column (so a default-value fill skips them).
    """
    if label not in ctx.listTimeseriesVariables():
        return set()
    n = ctx.getTimeseriesLength(label)
    return {
        (
            str(ctx.queryTimeseriesDate(label, i)),
            str(ctx.queryTimeseriesTime(label, i)),
        )
        for i in range(n)
    }


def _cleanup_pyhelios_cells(ctx, cells: list[tuple[str, Any, Any]]) -> None:
    """Best-effort cleanup: write NaN to cells we partially wrote during a
    column add that's now being rolled back.

    PyHelios v0.1.19 has no true delete; NaN is the closest workaround. If
    this cleanup itself fails, we swallow it — the SQL rollback already
    happened, so we accept a small PyHelios leak rather than crash.

    This is the "for later" atomicity gap noted in the design doc.
    """
    for label, d, t in cells:
        try:
            ctx.updateTimeseriesData(label, d, t, float("nan"))
        except Exception:
            pass


def add_columns(
    sctx: "ScenarioContext", columns: list["AddColumn"], db: Session
) -> dict:
    """Add one or more columns. Two-store atomic write.

    For each column:
      - persists a row in `weather_data_headers` (SQL),
      - writes the cells into PyHelios under label `str(header.id)`.

    Single transaction wraps all N columns + all M cells. Empty list is
    rejected (400). On any mid-batch failure the SQL transaction rolls
    back and PyHelios cells we wrote are NaN'd as best-effort cleanup
    (PyHelios v0.1.19 has no true delete).
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")

    if len(columns) == 0:
        raise HTTPException(400, "column list cannot be empty")

    ctx = sctx.context

    # ── Validation pass (fail-fast before any mutation) ──
    existing_header_names = {
        row[0]
        for row in db.query(WeatherDataHeader.name)
        .filter(WeatherDataHeader.scenario_id == sctx.scenario_id)
        .all()
    }

    for i, col in enumerate(columns):
        # 4a — reserved name
        if col.name in ("date", "time"):
            raise HTTPException(
                400, f"column[{i}]: name '{col.name}' is reserved"
            )

        # 4b — UNIQUE(scenario_id, name)
        if col.name in existing_header_names:
            raise HTTPException(
                409,
                f"column[{i}]: name '{col.name}' already exists in scenario",
            )

        # 4c — datatype FK
        if col.datatype is not None:
            if db.get(HeliosDataType, col.datatype) is None:
                raise HTTPException(
                    404, f"column[{i}]: datatype {col.datatype} not found"
                )

        # 4d — data_unit FK (and fetch it for the consistency check)
        unit_row = None
        if col.data_unit is not None:
            unit_row = db.get(DataUnit, col.data_unit)
            if unit_row is None:
                raise HTTPException(
                    404,
                    f"column[{i}]: data_unit {col.data_unit} not found",
                )

        # 4e — unit/type consistency
        if col.datatype is not None and unit_row is not None:
            if unit_row.data_type_id != col.datatype:
                raise HTTPException(
                    400,
                    f"column[{i}]: unit '{unit_row.unit}' belongs to "
                    f"data_type {unit_row.data_type_id}, not {col.datatype}",
                )

        # 4f — per-value parsing
        for j, v in enumerate(col.values):
            if not v.date or not v.time:
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: date and time are required",
                )
            try:
                list(int(p) for p in v.date.split("-"))
                list(int(p) for p in v.time.split(":"))
            except (ValueError, AttributeError):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: bad date/time format "
                    f"'{v.date}' '{v.time}'",
                )
            if v.value != "" and not _is_numeric_or_empty(v.value):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: value '{v.value}' "
                    f"is not numeric or empty",
                )
            if _has_excess_decimals(v.value):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: value '{v.value}' contains more than 7 decimal places"
                )

        if _has_excess_decimals(col.default_value):
            raise HTTPException(
                400,
                f"column[{i}]: default_value '{col.default_value}' contains more than 7 decimal places"
            )

        # Track newly-added names so a follow-up item in the same request
        # collides too (Pydantic also catches this, defence-in-depth).
        existing_header_names.add(col.name)

    # ── Atomic write: all N columns + all M cells in one transaction ──
    order_start = (
        db.query(WeatherDataHeader)
        .filter(WeatherDataHeader.scenario_id == sctx.scenario_id)
        .count()
    )
    written_cells: list[tuple[str, Any, Any]] = []
    created_columns: list[dict] = []

    # Snapshot scenario timestamps ONCE before any writes — otherwise the
    # newly-created (and partially-filled) label could become the anchor
    # and we'd see only the cells we just wrote.
    scenario_ts = _scenario_timestamps(ctx)

    try:
        for i, col in enumerate(columns):
            header = WeatherDataHeader(
                scenario_id=sctx.scenario_id,
                name=col.name,
                helios_data_type_id=col.datatype,
                unit_id=col.data_unit,
                status=1,
                display_order=order_start + i,
            )
            db.add(header)
            db.flush()  # populate header.id

            label = str(header.id)

            # Track explicit (date, time) keys so the fill loop knows which
            # scenario timestamps the user already covered via values[].
            # Empty value ("") still counts as explicit — written as NaN.
            explicit_keys: set[tuple[str, str]] = set()
            for v in col.values:
                d, t = _helios_date_time(v.date, v.time)
                cell_value = float("nan") if v.value == "" else float(v.value)
                ctx.addTimeseriesData(label, cell_value, d, t)
                written_cells.append((label, d, t))
                explicit_keys.add((v.date, v.time))

            # Fill every scenario timestamp not covered by values[]:
            #   - default_value if provided
            #   - else NaN (placeholder so the column is always aligned with
            #     the scenario's row count; matches addRow's NaN convention).
            fill_value = (
                float(col.default_value)
                if col.default_value is not None
                else float("nan")
            )
            for d_obj, t_obj in scenario_ts:
                key = (str(d_obj), str(t_obj))
                if key in explicit_keys:
                    continue
                ctx.addTimeseriesData(label, fill_value, d_obj, t_obj)
                written_cells.append((label, d_obj, t_obj))

            created_columns.append(
                {
                    "id": header.id,
                    "name": header.name,
                    "datatype_id": header.helios_data_type_id,
                    "data_unit_id": header.unit_id,
                }
            )

        db.commit()
        trigger_autosave(ctx, sctx.project_id)

    except HTTPException:
        db.rollback()
        _cleanup_pyhelios_cells(ctx, written_cells)
        raise
    except Exception as exc:
        db.rollback()
        _cleanup_pyhelios_cells(ctx, written_cells)
        raise HTTPException(500, f"Failed to add columns: {exc}")

    return {"success": True, "columns": created_columns}


def update_columns(
    sctx: "ScenarioContext", columns: list["AddColumn"], db: Session
) -> dict:
    """PATCH /updateCol — update one or more existing columns.

    Each item identifies the target column by `name` (must exist for this
    scenario; 404 otherwise). Per item:
      - `datatype` / `data_unit`: PATCH semantics — only updated when non-null.
        Consistency check (`unit.data_type_id == helios_data_type_id`) runs
        against the post-update pair when both are non-null.
      - `values[]`: each cell upserts. If a cell at (date, time) exists,
        overwrite via `updateTimeseriesData`; if not, create via
        `addTimeseriesData`.
      - `default_value`: for every scenario timestamp not in `values[]`
        where the column has no cell yet, write `default_value`. Does NOT
        overwrite existing cells — fill-only.

    Atomic: a per-item failure rolls back SQL and best-effort NaN-clears
    cells written during this batch.
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")

    if len(columns) == 0:
        raise HTTPException(400, "column list cannot be empty")

    ctx = sctx.context

    # ── Pre-load existing headers for this scenario, keyed by name ──
    existing_by_name = {
        h.name: h
        for h in db.query(WeatherDataHeader)
        .filter(WeatherDataHeader.scenario_id == sctx.scenario_id)
        .all()
    }

    # ── Validation pass (fail-fast before any mutation) ──
    for i, col in enumerate(columns):
        if col.name in ("date", "time"):
            raise HTTPException(
                400, f"column[{i}]: name '{col.name}' is reserved"
            )

        existing = existing_by_name.get(col.name)
        if existing is None:
            raise HTTPException(
                404,
                f"column[{i}]: name '{col.name}' not found in scenario",
            )

        # FK existence checks (only for fields the user is actually
        # sending — true PATCH semantics).
        if col.datatype is not None:
            if db.get(HeliosDataType, col.datatype) is None:
                raise HTTPException(
                    404, f"column[{i}]: datatype {col.datatype} not found"
                )

        new_unit_row = None
        if col.data_unit is not None:
            new_unit_row = db.get(DataUnit, col.data_unit)
            if new_unit_row is None:
                raise HTTPException(
                    404,
                    f"column[{i}]: data_unit {col.data_unit} not found",
                )

        # Consistency check against post-update pair. Only enforced when
        # both end up non-null; one-sided partial mappings remain legal.
        final_dt = (
            col.datatype if col.datatype is not None
            else existing.helios_data_type_id
        )
        final_unit = (
            col.data_unit if col.data_unit is not None
            else existing.unit_id
        )
        if final_dt is not None and final_unit is not None:
            unit_for_check = (
                new_unit_row if new_unit_row is not None
                else db.get(DataUnit, final_unit)
            )
            if unit_for_check.data_type_id != final_dt:
                raise HTTPException(
                    400,
                    f"column[{i}]: unit '{unit_for_check.unit}' belongs to "
                    f"data_type {unit_for_check.data_type_id}, not {final_dt}",
                )

        # Per-value parsing (same shape checks as addCol).
        for j, v in enumerate(col.values):
            if not v.date or not v.time:
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: date and time are required",
                )
            try:
                list(int(p) for p in v.date.split("-"))
                list(int(p) for p in v.time.split(":"))
            except (ValueError, AttributeError):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: bad date/time format "
                    f"'{v.date}' '{v.time}'",
                )
            if v.value != "" and not _is_numeric_or_empty(v.value):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: value '{v.value}' "
                    f"is not numeric or empty",
                )
            if _has_excess_decimals(v.value):
                raise HTTPException(
                    400,
                    f"column[{i}].values[{j}]: value '{v.value}' contains more than 7 decimal places"
                )

        if _has_excess_decimals(col.default_value):
            raise HTTPException(
                400,
                f"column[{i}]: default_value '{col.default_value}' contains more than 7 decimal places"
            )

    # ── Apply changes ──
    written_cells: list[tuple[str, Any, Any]] = []
    updated_columns: list[dict] = []

    # Snapshot scenario timestamps once at the top, before any writes.
    # We INCLUDE the target labels — they hold the pre-mutation state and
    # any of them is a valid anchor for the scenario's timeline. Excluding
    # them used to break the case where the only column being updated is
    # also the only column in the scenario (no other label to anchor on).
    scenario_ts = _scenario_timestamps(ctx)

    try:
        for i, col in enumerate(columns):
            existing = existing_by_name[col.name]
            label = str(existing.id)

            # SQL header field updates (PATCH semantics — None = no change).
            if col.datatype is not None:
                existing.helios_data_type_id = col.datatype
            if col.data_unit is not None:
                existing.unit_id = col.data_unit

            # Cells that already exist for this column (so we know what to
            # upsert vs. create, and which scenario timestamps default_value
            # should skip).
            existing_cell_keys = _label_timestamp_set(ctx, label)

            # Track keys provided in `values[]` so default_value skips them.
            explicit_keys: set[tuple[str, str]] = set()

            # Process explicit values[]. Empty value ("") writes NaN —
            # consistent with addRow's empty-cell convention.
            for v in col.values:
                d, t = _helios_date_time(v.date, v.time)
                key = (str(d), str(t))
                explicit_keys.add(key)
                cell_value = float("nan") if v.value == "" else float(v.value)

                if key in existing_cell_keys:
                    ctx.updateTimeseriesData(label, d, t, cell_value)
                else:
                    ctx.addTimeseriesData(label, cell_value, d, t)
                    written_cells.append((label, d, t))
                    existing_cell_keys.add(key)

            # Fill scenario timestamps not in values[]:
            #   - default_value provided  → OVERWRITE every such cell
            #     (use case: select-all / deselect-all on a check column).
            #   - default_value missing   → fill ONLY missing cells with NaN
            #     (so the column stays aligned with the scenario timeline,
            #     but existing data is left alone — non-destructive PATCH).
            if col.default_value is not None:
                fill_value = float(col.default_value)
                for d_obj, t_obj in scenario_ts:
                    key = (str(d_obj), str(t_obj))
                    if key in explicit_keys:
                        continue  # explicit value wins
                    if key in existing_cell_keys:
                        ctx.updateTimeseriesData(label, d_obj, t_obj, fill_value)
                    else:
                        ctx.addTimeseriesData(label, fill_value, d_obj, t_obj)
                        written_cells.append((label, d_obj, t_obj))
                        existing_cell_keys.add(key)
            else:
                for d_obj, t_obj in scenario_ts:
                    key = (str(d_obj), str(t_obj))
                    if key in explicit_keys or key in existing_cell_keys:
                        continue  # don't overwrite existing data
                    ctx.addTimeseriesData(
                        label, float("nan"), d_obj, t_obj
                    )
                    written_cells.append((label, d_obj, t_obj))
                    existing_cell_keys.add(key)

            updated_columns.append(
                {
                    "id": existing.id,
                    "name": existing.name,
                    "datatype_id": existing.helios_data_type_id,
                    "data_unit_id": existing.unit_id,
                }
            )

        db.commit()
        trigger_autosave(ctx, sctx.project_id)

    except HTTPException:
        db.rollback()
        _cleanup_pyhelios_cells(ctx, written_cells)
        raise
    except Exception as exc:
        db.rollback()
        _cleanup_pyhelios_cells(ctx, written_cells)
        raise HTTPException(500, f"Failed to update columns: {exc}")

    return {"success": True, "columns": updated_columns}


def add_rows(
    sctx: "ScenarioContext", rows: list[dict[str, Any]], db: Session
) -> dict:
    """Append rows to the timeseries table.

    Each row must include `date` + `time` and exactly the set of header
    ids (stringified) registered in `weather_data_headers` for this
    scenario. SQL is the source of truth for "which columns this scenario
    has" — that lets a column added via /addCol with values=[] be a legal
    /addRow target before any PyHelios cell exists for it.

    Trade-off: labels that exist in PyHelios but NOT in SQL — e.g.
    CSV-column names registered via /uploadfile — are not valid /addRow
    targets. Those flows write rows through their own paths.

    PyHelios silently drops duplicate timestamps, so we reject duplicates
    upfront (both within the batch and against existing rows).
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")

    ctx = sctx.context

    # ── Label set from SQL: every header id (str) for this scenario.
    # Headers named "date" or "time" are excluded — those names are
    # reserved as row keys, not data columns. addCol/PATCH refuse to
    # create them, but the bulk PUT doesn't enforce that, so a sideloaded
    # row could exist; this filter keeps add_rows robust either way. ──
    header_ids = [
        row[0]
        for row in db.query(WeatherDataHeader.id)
        .filter(WeatherDataHeader.scenario_id == sctx.scenario_id)
        .filter(WeatherDataHeader.name.notin_(("date", "time")))
        .all()
    ]
    existing_label_set = {str(hid) for hid in header_ids}

    # ── Existing timestamps still come from PyHelios (SQL doesn't track
    # row timestamps). We can only anchor on a label PyHelios actually
    # has registered — empty-addCol headers have no cells yet. ──
    pyhelios_labels = list(ctx.listTimeseriesVariables())
    existing_timestamps: list[tuple[Any, Any]] = []
    if pyhelios_labels:
        anchor = pyhelios_labels[0]
        n = ctx.getTimeseriesLength(anchor)
        for i in range(n):
            existing_timestamps.append(
                (
                    ctx.queryTimeseriesDate(anchor, i),
                    ctx.queryTimeseriesTime(anchor, i),
                )
            )
    existing_timestamp_keys = {
        (str(d), str(t)) for d, t in existing_timestamps
    }

    # ── Validation ──
    batch_keys: set[tuple[str, str]] = set()
    for row_data in rows:
        date_val = row_data.get("date")
        time_val = row_data.get("time")
        if date_val in (None, ""):
            raise HTTPException(400, "date is required")
        if time_val in (None, ""):
            raise HTTPException(400, "time is required")

        key = (date_val, time_val)
        if key in batch_keys:
            raise HTTPException(400, f"duplicate timestamp in batch: {key}")
        if key in existing_timestamp_keys:
            raise HTTPException(400, f"timestamp already exists: {key}")
        batch_keys.add(key)

        row_labels = {k for k in row_data.keys() if k not in ("date", "time")}
        if row_labels != existing_label_set:
            missing = existing_label_set - row_labels
            unknown = row_labels - existing_label_set
            raise HTTPException(
                400,
                f"row labels must match existing columns; "
                f"missing={sorted(missing)}, unknown={sorted(unknown)}",
            )

    # ── Write ──
    # Empty values are written as NaN (preserves the row at this timestamp
    # for every column). Non-numeric values are rejected with 400 rather
    # than silently skipped — that catches typos like "abc" instead of "1.5".
    added_rows = 0
    for row_data in rows:
        cells: dict[str, float] = {}
        for k, v in row_data.items():
            if k in ("date", "time"):
                continue
            vstr = v if isinstance(v, str) else str(v)
            if vstr.strip() == "":
                cells[k] = float("nan")
            elif _is_numeric_or_empty(vstr):
                if _has_excess_decimals(vstr):
                    raise HTTPException(
                        400, f"value '{v}' for column '{k}' contains more than 7 decimal places"
                    )
                cells[k] = float(vstr)
            else:
                raise HTTPException(
                    400,
                    f"value '{v}' for column '{k}' is not numeric or empty",
                )
        _add_row(ctx, row_data["date"], row_data["time"], cells)
        added_rows += 1

    trigger_autosave(ctx, sctx.project_id)
    return {
        "success": True,
        "row_count": len(existing_timestamps) + len(rows),
        "column_count": 2 + len(existing_label_set),
        "added_rows": added_rows,
    }


# ─── update — batch update of existing cells ────────────────────────────────


def update_cells(sctx: "ScenarioContext", req: "UpdateRequest") -> dict:
    """Update one or more existing cells in a single call.

    Used by the frontend when changing a column's data_unit — every cell
    in that column needs to be rewritten with the converted value. Each
    item is independent: same column allowed across items, or different
    columns mixed in.

    Fail-fast on the first error. PyHelios isn't transactional and has no
    remove API, so any items processed before the failing one stay
    applied. The error includes the item index so the frontend can
    pinpoint which one failed.
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")
    ctx = sctx.context

    if len(req.updates) == 0:
        raise HTTPException(400, "updates list cannot be empty")

    for i, item in enumerate(req.updates):
        # Business rule — PyHelios doesn't reserve these names, we do.
        if item.col in ("date", "time"):
            raise HTTPException(
                400, f"updates[{i}]: cannot update the date/time column"
            )

        date_obj, time_obj = _helios_date_time(item.row.date, item.row.time)

        # PyHelios validates everything else for us:
        #   - missing column          → HeliosRuntimeError
        #   - missing cell at (d, t)  → HeliosRuntimeError
        #   - non-numeric value       → ValueError (from float() cast)
        try:
            if _has_excess_decimals(item.value):
                raise HTTPException(
                    400, f"updates[{i}]: value '{item.value}' contains more than 7 decimal places"
                )
            new_value = float("nan") if item.value == "" else float(item.value)
            ctx.updateTimeseriesData(item.col, date_obj, time_obj, new_value)
        except ValueError as exc:
            raise HTTPException(
                400, f"updates[{i}]: value '{item.value}' is not numeric: {exc}"
            )
        except HeliosRuntimeError as exc:
            raise HTTPException(404, f"updates[{i}]: {exc}")

    trigger_autosave(ctx, sctx.project_id)
    return {"success": True, "updated_count": len(req.updates)}


# ─── delete — direct updateTimeseriesData calls (no wrappers) ────────────────


def delete(sctx: "ScenarioContext", req: "DeleteRequest", db: Session) -> dict:
    """Remove a row, a column, or wipe everything.

    - Whole wipe: clearTimeseriesData (PyHelios) + delete all headers (SQL).
    - Row delete: updateTimeseriesData(..., NaN) for that timestamp across all vars.
    - Column delete: deleteTimeseriesVariable (PyHelios) + delete header (SQL).
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")
    ctx = sctx.context

    if req.row is None and req.column is None:
        # Wipe EVERYTHING for this scenario
        ctx.clearTimeseriesData()
        db.query(WeatherDataHeader).filter_by(scenario_id=sctx.scenario_id).delete()
        db.commit()
        return {"success": True, "row_count": 0, "column_count": 2}

    # STEP A — clear one row
    if req.row is not None:
        date_obj, time_obj = _helios_date_time(req.row.date, req.row.time)
        labels = list(ctx.listTimeseriesVariables())
        if not any(_label_has_timestamp(ctx, lbl, date_obj, time_obj) for lbl in labels):
            raise HTTPException(
                404,
                f"no row at {req.row.date} {req.row.time}",
            )
        for label in labels:
            if _label_has_timestamp(ctx, label, date_obj, time_obj):
                ctx.updateTimeseriesData(label, date_obj, time_obj, float("nan"))

    # STEP B — delete one column
    if req.column is not None:
        name = req.column.columnname
        if name in ("date", "time"):
            raise HTTPException(400, "cannot delete the date/time column")

        # 1. Find the header in DB
        h_row = (
            db.query(WeatherDataHeader)
            .filter_by(scenario_id=sctx.scenario_id, name=name)
            .first()
        )
        if not h_row:
            raise HTTPException(404, f"column '{name}' not found")

        # 2. Delete from DB (UI)
        db.delete(h_row)
        db.commit()

        # 3. Delete from Simulation (RAM) using the new v0.1.19 method.
        # We use str(h_row.id) because that is the internal label.
        try:
            ctx.deleteTimeseriesVariable(str(h_row.id))
        except HeliosRuntimeError:
            # If it's missing in RAM but deleted in DB, we consider it a success
            pass

    labels_after = list(ctx.listTimeseriesVariables())
    row_count = ctx.getTimeseriesLength(labels_after[0]) if labels_after else 0
    column_count = 2 + len(labels_after)
    trigger_autosave(ctx, sctx.project_id)
    return {
        "success": True,
        "row_count": row_count,
        "column_count": column_count,
    }


# ─── clear_data — clear both stores ──────────────────────────────────────────


def clear_data(sctx: "ScenarioContext", db: Session) -> dict:
    """Clear everything for the scenario: SQL headers + PyHelios cells.

    Order: SQL delete first (transactional), then PyHelios clearTimeseriesData
    as best-effort. Mirrors the pattern in `delete_header`. If the PyHelios
    call fails, the SQL state is still consistent (no headers, no metadata),
    and any leaked PyHelios cells are orphans that don't affect /addRow
    since the label set is now empty.
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")

    try:
        headers_removed = (
            db.query(WeatherDataHeader)
            .filter(WeatherDataHeader.scenario_id == sctx.scenario_id)
            .delete()
        )
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(500, "Failed to clear scenario weather state")

    try:
        sctx.context.clearTimeseriesData()
    except Exception:
        pass  # SQL is the source of truth; orphan cells will be invisible to /addRow

    trigger_autosave(sctx.context, sctx.project_id)
    return {
        "success": True,
        "headers_removed": headers_removed,
        "row_count": 0,
        "column_count": 2,
    }
