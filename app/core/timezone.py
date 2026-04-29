"""
Resolve UTC offset (ISO 8601 string, e.g. "+05:30") from geographic
coordinates.

Uses timezonefinder to look up the IANA timezone name, then zoneinfo
(stdlib, Python 3.9+) to get the current wall-clock UTC offset.

Windows note: zoneinfo needs the `tzdata` pip package because Windows
doesn't ship an IANA tz database. Listed in requirements.txt under a
sys_platform marker.

Returns "+00:00" if coordinates are invalid or the timezone cannot be
resolved. Unexpected failures are logged before falling back so silent
breakage doesn't recur.
"""
import logging
from datetime import datetime

logger = logging.getLogger(__name__)


def utc_offset_from_coords(latitude: float, longitude: float) -> str:
    """
    Return the UTC offset as an ISO 8601 string ("+HH:MM" or "-HH:MM").

    Examples: IST (Asia/Kolkata) → "+05:30", MST (America/Denver) → "-07:00",
    Nepal Time → "+05:45". The offset reflects DST at the time of the call.
    """
    try:
        from timezonefinder import TimezoneFinder
        from zoneinfo import ZoneInfo

        tf = TimezoneFinder()
        tz_name = tf.timezone_at(lat=latitude, lng=longitude)
        if tz_name is None:
            return "+00:00"

        tz = ZoneInfo(tz_name)
        now = datetime.now(tz=tz)
        offset = now.utcoffset()
        if offset is None:
            return "+00:00"

        total_minutes = int(round(offset.total_seconds() / 60))
        sign = "+" if total_minutes >= 0 else "-"
        abs_minutes = abs(total_minutes)
        hours, minutes = divmod(abs_minutes, 60)
        return f"{sign}{hours:02d}:{minutes:02d}"

    except Exception:
        logger.exception(
            "utc_offset_from_coords failed for (%s, %s); falling back to +00:00",
            latitude, longitude,
        )
        return "+00:00"
