"""Central timezone helpers for host-independent Asia/Kolkata datetimes."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone, tzinfo

try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except ImportError:  # pragma: no cover - Python 3.9+ always provides zoneinfo.
    ZoneInfo = None  # type: ignore[assignment,misc]
    ZoneInfoNotFoundError = Exception  # type: ignore[assignment,misc]


IST_TIMEZONE_NAME = "Asia/Kolkata"


def _load_ist_timezone() -> tzinfo:
    """Prefer IANA zoneinfo, then pytz, then India's fixed UTC+05:30 offset.

    Some Windows Python installations do not ship the IANA timezone database.
    India has no daylight-saving transitions, so the stdlib fixed-offset final
    fallback preserves correct IST behavior without adding a dependency.
    """

    if ZoneInfo is not None:
        try:
            return ZoneInfo(IST_TIMEZONE_NAME)
        except ZoneInfoNotFoundError:
            pass

    try:
        import pytz

        return pytz.timezone(IST_TIMEZONE_NAME)
    except ImportError:
        return timezone(timedelta(hours=5, minutes=30), name=IST_TIMEZONE_NAME)


IST = _load_ist_timezone()


def localize_ist(value: datetime) -> datetime:
    """Attach or convert a datetime to IST without consulting host local time."""

    if value.tzinfo is None or value.utcoffset() is None:
        # pytz needs localize(); zoneinfo and datetime.timezone use replace().
        localize = getattr(IST, "localize", None)
        if callable(localize):
            return localize(value)
        return value.replace(tzinfo=IST)
    return value.astimezone(IST)


def parse_ist_datetime(value: str) -> datetime:
    """Parse ISO-8601 and return an aware IST datetime.

    A timestamp without an offset is explicitly interpreted as IST. A timestamp
    with another offset is converted to the equivalent instant in IST.
    """

    return localize_ist(datetime.fromisoformat(value))


def combine_ist(day: date, clock: time) -> datetime:
    """Combine calendar values into an aware IST datetime."""

    return localize_ist(datetime.combine(day, clock))


def ist_isoformat(value: datetime) -> str:
    """Serialize any datetime as an explicit IST ISO-8601 timestamp."""

    return localize_ist(value).isoformat()
