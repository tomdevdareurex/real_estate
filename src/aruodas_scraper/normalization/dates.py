"""Date normalization helpers."""

from datetime import date


def parse_iso_date(value: str | None) -> date | None:
    """Parse an ISO date or return null when unavailable or malformed."""
    if value is None:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None
