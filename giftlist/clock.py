"""Time helpers. Stored times are UTC; calendar rules use Irish time."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Dublin")


def now() -> datetime:
    return datetime.now(UTC)


def local_today() -> date:
    return datetime.now(LOCAL).date()
