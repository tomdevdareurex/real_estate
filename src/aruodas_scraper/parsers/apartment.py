"""Apartment detail parser."""

from pathlib import Path

from aruodas_scraper.models import ListingRecord, UnknownField
from aruodas_scraper.parsers.common import parse_listing


def parse_apartment(
    html: str,
    source_search_url: str,
    page: int,
    mapping_path: Path | None = None,
) -> tuple[ListingRecord, tuple[UnknownField, ...]]:
    """Parse a saved Aruodas apartment detail page."""
    return parse_listing(html, "apartment", source_search_url, page, mapping_path)
