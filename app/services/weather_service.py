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

from app.helios import context as helios_ctx

if TYPE_CHECKING:
    from app.core.scenario_context import ScenarioContext
    from app.schemas.weather import AddRequest, DeleteRequest, UpdateRequest


# ─── Shared helpers ──────────────────────────────────────────────────────────


def _helios_date_time(date_str: str, time_str: str):
    """Parse 'YYYY-MM-DD' + 'HH:MM:SS' into PyHelios (Date, Time) objects."""
    try:
        y, mo, d = (int(p) for p in date_str.split("-"))
        hh, mm, ss = (int(p) for p in time_str.split(":"))
    except (ValueError, AttributeError):
        raise HTTPException(400, f"bad date/time: '{date_str}' '{time_str}'")
    return helios_ctx.Date(y, mo, d), helios_ctx.Time(hh, mm, ss)


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
    """NaN → None so JSON serialization doesn't choke. Otherwise float."""
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return None
    return float(v)


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


def _transform_csv(raw_bytes: bytes) -> tuple[list[str], list[list[str]]]:
    """Take raw CSV bytes in any reasonable format, return (header, rows) in
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

    # Deduplicate by (date, time). Keep the LAST row for each pair — PyHelios
    # silently drops duplicate timestamps with only a stderr warning.
    seen: dict[tuple[str, str], int] = {}
    for idx, row in enumerate(new_rows):
        key = (row[0], row[1])
        seen[key] = idx
    if len(seen) < len(new_rows):
        new_rows = [new_rows[i] for i in sorted(seen.values())]

    return new_header, new_rows


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

    header, rows = _transform_csv(file_bytes)

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

    return {
        "success": True,
        "row_count": len(rows),
        "column_count": len(header),
    }


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


def add(sctx: "ScenarioContext", req: "AddRequest") -> dict:
    """Surgical add: one column per request, any number of rows per request."""
    if req.rows is None and req.column is None:
        raise HTTPException(400, "must specify `column`, `rows`, or both")
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")
    ctx = sctx.context

    # ── Validation pass — no mutations until every input passes ──
    existing_labels = list(ctx.listTimeseriesVariables())
    existing_timestamps: list[tuple[Any, Any]] = []
    if existing_labels:
        anchor = existing_labels[0]
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

    slug: str | None = None
    if req.column is not None:
        slug = _slugify(req.column.columnname)
        if not slug or slug in ("date", "time"):
            raise HTTPException(400, "invalid column name")
        if slug in existing_labels:
            raise HTTPException(400, f"column '{slug}' already exists")
        for v in req.column.values:
            if not _is_numeric_or_empty(v if isinstance(v, str) else str(v)):
                raise HTTPException(400, f"column value '{v}' is not numeric or empty")

    if req.rows is not None:
        batch_keys: set[tuple[str, str]] = set()
        existing_label_set = set(existing_labels)

        for row_data in req.rows:
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
            # No per-cell value check — empty / non-numeric cells become no-ops
            # at insert time. All-empty rows are silent no-ops; the user can
            # fill values later via another /add on the same (date, time).

    # ── STEP A — add ONE column (if requested) ──
    if req.column is not None and slug is not None:
        aligned: list[tuple[Any, Any, float]] = []
        for i, value in enumerate(req.column.values):
            if i >= len(existing_timestamps):
                break
            vstr = value if isinstance(value, str) else str(value)
            if _is_numeric_or_empty(vstr) and vstr.strip():
                d, t = existing_timestamps[i]
                aligned.append((d, t, float(vstr)))
        _add_column(ctx, slug, aligned)

    # ── STEP B — add rows (one wrapper call per row) ──
    added_rows = 0
    if req.rows is not None:
        for row_data in req.rows:
            cells: dict[str, float] = {}
            for k, v in row_data.items():
                if k in ("date", "time"):
                    continue
                vstr = v if isinstance(v, str) else str(v)
                if _is_numeric_value(v) or (isinstance(v, str) and _is_numeric_or_empty(vstr) and vstr.strip()):
                    cells[k] = float(vstr)
            if cells:
                _add_row(ctx, row_data["date"], row_data["time"], cells)
            added_rows += 1

    row_count = len(existing_timestamps) + (len(req.rows) if req.rows else 0)
    column_count = 2 + len(ctx.listTimeseriesVariables())
    result: dict[str, Any] = {
        "success": True,
        "row_count": row_count,
        "column_count": column_count,
    }
    if req.column is not None:
        result["added_column"] = slug
    if req.rows is not None:
        result["added_rows"] = added_rows
    return result


# ─── update — pre-checks the cell exists ─────────────────────────────────────


def update_cell(sctx: "ScenarioContext", req: "UpdateRequest") -> dict:
    """Update ONE existing cell. Returns 404 if the cell doesn't exist."""
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")
    ctx = sctx.context

    if req.col in ("date", "time"):
        raise HTTPException(400, "cannot update the date/time column")
    if req.col not in ctx.listTimeseriesVariables():
        raise HTTPException(404, f"column '{req.col}' not found")
    if req.value != "" and not _is_numeric_or_empty(req.value):
        raise HTTPException(400, f"value '{req.value}' is not numeric or empty")

    date_obj, time_obj = _helios_date_time(req.row.date, req.row.time)

    if not _label_has_timestamp(ctx, req.col, date_obj, time_obj):
        raise HTTPException(
            404,
            f"no cell at {req.row.date} {req.row.time} for column '{req.col}'",
        )

    # updateTimeseriesData signature: (label, Date, Time, new_value). Value LAST.
    if req.value == "":
        ctx.updateTimeseriesData(req.col, date_obj, time_obj, float("nan"))
    else:
        ctx.updateTimeseriesData(req.col, date_obj, time_obj, float(req.value))

    return {"success": True}


# ─── delete — direct updateTimeseriesData calls (no wrappers) ────────────────


def delete(sctx: "ScenarioContext", req: "DeleteRequest") -> dict:
    """Remove a row, a column, or wipe everything.

    CAVEAT: partial deletes write NaN via updateTimeseriesData. The data point
    stays in PyHelios memory (label still listed, length unchanged, queries
    return NaN). A true remove would need removeTimeseriesData /
    removeTimeseriesVariable — not in v0.1.19.
    """
    if not helios_ctx.PYHELIOS_AVAILABLE or sctx.context is None:
        raise HTTPException(503, "PyHelios not available")
    ctx = sctx.context

    if req.row is None and req.column is None:
        ctx.clearTimeseriesData()
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

    # STEP B — clear one column
    if req.column is not None:
        name = req.column.columnname
        if name in ("date", "time"):
            raise HTTPException(400, "cannot delete the date/time column")
        if name not in ctx.listTimeseriesVariables():
            raise HTTPException(404, f"column '{name}' not found")
        n = ctx.getTimeseriesLength(name)
        for i in range(n):
            date_obj = ctx.queryTimeseriesDate(name, i)
            time_obj = ctx.queryTimeseriesTime(name, i)
            ctx.updateTimeseriesData(name, date_obj, time_obj, float("nan"))

    labels_after = list(ctx.listTimeseriesVariables())
    row_count = ctx.getTimeseriesLength(labels_after[0]) if labels_after else 0
    column_count = 2 + len(labels_after)
    return {
        "success": True,
        "row_count": row_count,
        "column_count": column_count,
    }
