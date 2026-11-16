"""Calendar days in one zone: `kpi.timezone` (decisions 0012 and 0014).

    parse_zone(text) -> tzinfo             an IANA name ("Asia/Tokyo", where the OS has the zone database), a fixed
                                           offset ("+09:00", "-05:30") or "UTC"; raises ValueError naming the fix
    set_zone(tz) / zone()                  the zone onloopd was configured with (default: this machine's offset now)
    local_date(ts, tz) -> date             the calendar day of a stored timestamp
    cost_from_days(n, ts, tz) -> str       the ingest day + n, as YYYY-MM-DD: how `days=<n>` becomes a date, once
    days_left(cost_from, now, tz) -> int   the days until `cost_from`, never below 0 ("costing now")

A count of days is never stored: `days=<n>` is turned into a date at ingest, from the event's ledger time, and every
count shown or sorted on is computed from that date when it is read.
"""

from __future__ import annotations

import datetime as dt
import re

from . import events as ev

_OFFSET = re.compile(r"^([+-])([01][0-9]|2[0-3]):([0-5][0-9])$")
_zone: dt.tzinfo | None = None


def parse_zone(text: str) -> dt.tzinfo:
    if not isinstance(text, str) or not text.strip():
        raise ValueError(
            "timezone must be an IANA name such as Asia/Tokyo, a fixed offset such as +09:00, or UTC"
        )
    text = text.strip()
    if text.upper() in ("UTC", "Z", "+00:00"):
        return dt.UTC
    m = _OFFSET.match(text)
    if m:
        sign = 1 if m.group(1) == "+" else -1
        return dt.timezone(sign * dt.timedelta(hours=int(m.group(2)), minutes=int(m.group(3))), text)
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(text)
    except Exception as e:  # ZoneInfoNotFoundError, ValueError on a malformed key
        raise ValueError(
            f"timezone {text!r} is not known on this machine; give a fixed offset such as +09:00, "
            "or install the time zone database (pip install tzdata)"
        ) from e


def local_offset() -> dt.tzinfo:
    """This machine's UTC offset now, as a fixed zone."""
    off = dt.datetime.now().astimezone().utcoffset() or dt.timedelta(0)
    return dt.timezone(off)


def set_zone(tz: dt.tzinfo | None) -> None:
    global _zone
    _zone = tz


def zone() -> dt.tzinfo:
    return _zone if _zone is not None else local_offset()


def local_date(ts: str, tz: dt.tzinfo | None = None) -> dt.date:
    return ev.parse_ts(ts).astimezone(tz or zone()).date()


def cost_from_days(days: int, ts: str, tz: dt.tzinfo | None = None) -> str:
    return (local_date(ts, tz) + dt.timedelta(days=int(days))).isoformat()


def days_left(cost_from: str, now: str, tz: dt.tzinfo | None = None) -> int:
    return max(0, (dt.date.fromisoformat(cost_from) - local_date(now, tz)).days)
