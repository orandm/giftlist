"""Time helpers. Stored times are UTC; calendar rules use Irish time."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

LOCAL = ZoneInfo("Europe/Dublin")

# The only timezones the daily digest picker offers -- (IANA name, label). Add
# to this if the family spreads further; nobody needs the full IANA list.
SUPPORTED_TIMEZONES: tuple[tuple[str, str], ...] = (
    ("Europe/Dublin", "Ireland"),
    ("America/Vancouver", "Vancouver"),
)


def now() -> datetime:
    return datetime.now(UTC)


def local_today() -> date:
    return datetime.now(LOCAL).date()


def time_ago(dt: datetime) -> str:
    """A short relative time, for the activity feed."""
    seconds = (now() - dt).total_seconds()
    if seconds < 60:
        return "just now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    return f"{int(seconds // 86400)}d ago"
