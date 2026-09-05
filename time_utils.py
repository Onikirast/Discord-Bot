"""
time_utils.py

Small parser that turns user-friendly time strings into UTC datetimes.

Supports:
  - Relative shorthand: "10m", "2h", "1d", "1h30m"
  - Absolute: "YYYY-MM-DD HH:MM" (interpreted as UTC for simplicity)

Kept deliberately simple and dependency-free. A natural extension
mentioned in the README is per-user timezone support via `zoneinfo`.
"""

import re
from datetime import datetime, timedelta, timezone

RELATIVE_PATTERN = re.compile(
    r"^(?:(\d+)d)?(?:(\d+)h)?(?:(\d+)m)?$", re.IGNORECASE
)


class TimeParseError(ValueError):
    pass


def parse_when(text: str) -> datetime:
    """Return a timezone-aware UTC datetime for the given input string, or
    raise TimeParseError.

    Both branches below must produce the same (aware) shape consistently:
    these values ultimately get stored as ISO strings and compared as
    plain TEXT in SQL, so a naive datetime from one branch and an aware
    one from the other would silently produce wrong comparisons rather
    than an error.
    """
    text = text.strip()

    # Try relative shorthand first, e.g. "2h", "30m", "1d12h"
    match = RELATIVE_PATTERN.match(text.replace(" ", ""))
    if match and any(match.groups()):
        days, hours, minutes = (int(g) if g else 0 for g in match.groups())
        delta = timedelta(days=days, hours=hours, minutes=minutes)
        if delta.total_seconds() <= 0:
            raise TimeParseError("Duration must be greater than zero.")
        return datetime.now(timezone.utc) + delta

    # Fall back to absolute "YYYY-MM-DD HH:MM", interpreted as UTC
    try:
        dt = datetime.strptime(text, "%Y-%m-%d %H:%M")
        return dt.replace(tzinfo=timezone.utc)
    except ValueError as e:
        raise TimeParseError(
            "Couldn't understand that time. Use something like '2h', '30m', "
            "'1d12h', or an absolute time like '2026-07-25 14:30'."
        ) from e


def parse_hhmm(text: str) -> str:
    """Validate and normalise a 'HH:MM' string."""
    try:
        dt = datetime.strptime(text.strip(), "%H:%M")
        return dt.strftime("%H:%M")
    except ValueError as e:
        raise TimeParseError("Time must be in 24h HH:MM format, e.g. '09:30'.") from e


DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def parse_day(text: str) -> int:
    """Return 0-6 (Monday=0) for a day name, or raise TimeParseError."""
    text = text.strip().lower()
    if text in DAY_NAMES:
        return DAY_NAMES.index(text)
    raise TimeParseError(f"Day must be one of: {', '.join(DAY_NAMES)}")
