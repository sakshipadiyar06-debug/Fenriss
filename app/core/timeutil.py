"""Timezone helpers.

Convention: machine timestamps (created_at, completed_at...) are stored as naive UTC.
Task deadlines are stored as naive *local* time of the user's timezone.
"""
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def get_tz(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "Asia/Kolkata")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("Asia/Kolkata")


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def local_now(tz) -> datetime:
    """Naive local datetime in tz."""
    return datetime.now(tz).replace(tzinfo=None)


def local_today(tz) -> date:
    return datetime.now(tz).date()


def to_local(dt_utc_naive: datetime, tz) -> datetime:
    return dt_utc_naive.replace(tzinfo=timezone.utc).astimezone(tz).replace(tzinfo=None)


def day_bounds_utc(d: date, tz) -> tuple[datetime, datetime]:
    def conv(x):
        return datetime.combine(x, time.min, tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)
    return conv(d), conv(d + timedelta(days=1))


def parse_hhmm(s: str, default: str = "00:00") -> time:
    try:
        h, m = (s or default).split(":")
        return time(int(h), int(m))
    except Exception:
        h, m = default.split(":")
        return time(int(h), int(m))
