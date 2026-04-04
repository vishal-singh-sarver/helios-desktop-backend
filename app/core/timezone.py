"""
Resolve UTC offset (fractional hours) from geographic coordinates.

Uses timezonefinder to look up the IANA timezone name, then zoneinfo
(stdlib, Python 3.9+) to get the current wall-clock UTC offset.

Returns 0.0 if coordinates are invalid or the timezone cannot be resolved.
"""
from datetime import datetime, timezone


def utc_offset_from_coords(latitude: float, longitude: float) -> float:
    """
    Return the UTC offset in fractional hours for the given lat/long.

    Example: IST (Asia/Kolkata) → 5.5,  MST (America/Denver) → -7.0
    The offset reflects DST at the time of the call.
    """
    try:
        from timezonefinder import TimezoneFinder
        from zoneinfo import ZoneInfo

        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=latitude, lng=longitude)
        if tz_name is None:
            return 0.0

        tz = ZoneInfo(tz_name)
        now = datetime.now(tz=tz)
        offset_seconds = now.utcoffset().total_seconds()
        return offset_seconds / 3600.0

    except Exception:
        return 0.0
