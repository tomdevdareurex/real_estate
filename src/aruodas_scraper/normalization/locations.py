"""Conservative parsing of displayed Lithuanian location hierarchy."""

from dataclasses import dataclass

from aruodas_scraper.normalization.text import clean_text


@dataclass(frozen=True, slots=True)
class LocationParts:
    """Location parts derived only from displayed address text."""

    city: str | None
    neighbourhood: str | None
    street: str | None
    full_address: str | None


def parse_displayed_address(value: str | None) -> LocationParts:
    """Split a displayed address without inventing unavailable hierarchy levels."""
    cleaned = clean_text(value)
    parts = [part.strip() for part in cleaned.split(",")] if cleaned else []
    return LocationParts(
        city=parts[0] if parts else None,
        neighbourhood=parts[1] if len(parts) > 1 else None,
        street=parts[2] if len(parts) > 2 else None,
        full_address=cleaned,
    )
