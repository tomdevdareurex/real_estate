"""House detail parser."""

from pathlib import Path

from aruodas_scraper.models import ListingRecord, UnknownField
from aruodas_scraper.parsers.common import parse_listing


def parse_house(
    html: str,
    source_search_url: str,
    page: int,
    mapping_path: Path | None = None,
    listing_type: str = "sale",
) -> tuple[ListingRecord, tuple[UnknownField, ...]]:
    """Parse a saved Aruodas house detail page."""
    return parse_listing(html, "house", source_search_url, page, mapping_path, listing_type)
