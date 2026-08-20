"""Parsing of search-result cards, which already carry most listing attributes.

One search page yields 25 cards, so harvesting them costs a single request where the
equivalent detail crawl would cost 25. Against a per-IP request-count budget that ratio
is the difference between a partial and a complete dataset.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from aruodas_scraper.cities import PropertyCategory
from aruodas_scraper.models import ListingRecord
from aruodas_scraper.normalization.numbers import parse_decimal, parse_integer
from aruodas_scraper.normalization.privacy import redact_contact_details
from aruodas_scraper.normalization.text import clean_text
from aruodas_scraper.normalization.translations import FieldMappings, load_field_mappings

_CARD_SELECTOR = "div.list-row-v2[data-uid]"
_UID_PATTERNS: dict[PropertyCategory, re.Pattern[str]] = {
    "apartments": re.compile(r"^1-\d+$"),
    "houses": re.compile(r"^2-\d+$"),
}
_FLOOR_OF_TOTAL = re.compile(r"^\s*(\d+)\s*/\s*(\d+)")
_BASE_URL = "https://www.aruodas.lt/"


def _node_text(scope: Node, selector: str) -> str | None:
    node = scope.css_first(selector)
    return clean_text(node.text(separator=" ")) if node else None


def _detail_value(card: Node, container_class: str) -> str | None:
    return _node_text(card, f".{container_class} .list-detail-value-v2")


def _canonicalize(href: str) -> str:
    """Resolve a card href to an absolute, query-free listing URL.

    Cards may carry a root-relative href. Storing one verbatim exports a `canonical_url` that
    no fetch can use, which only surfaces later when a deepening run tries to follow it.
    """
    parts = urlsplit(urljoin(_BASE_URL, href))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _search_position(href: str) -> int | None:
    values = parse_qs(urlsplit(href).query).get("search_pos")
    return parse_integer(values[0]) if values else None


def _address(anchor: Node) -> tuple[str | None, str | None]:
    """Split the two-line card address into district and street."""
    strong = anchor.css_first("strong")
    street = clean_text(strong.text(separator=" ")) if strong else None
    parts = (clean_text(part) for part in anchor.text(separator="\n").split("\n"))
    lines = [line for line in parts if line]
    district = next((line for line in lines if line != street), None)
    return district, street


def _floors(value: str | None) -> tuple[int | None, int | None]:
    """Split ``"2/4 aukšt."`` into the listing floor and the building's floor count."""
    if value is None:
        return None, None
    match = _FLOOR_OF_TOTAL.match(value)
    if match is None:
        return None, parse_integer(value)
    return int(match.group(1)), int(match.group(2))


def _image_urls(card: Node) -> tuple[str, ...]:
    node = card.css_first("img[data-src]")
    if node is None:
        return ()
    candidates = [str(node.attributes.get("data-src") or "")]
    extra = node.attributes.get("data-extra")
    if isinstance(extra, str):
        candidates.extend(part.strip() for part in extra.split(","))
    ordered: dict[str, None] = {}
    for url in candidates:
        if url:
            ordered.setdefault(url, None)
    return tuple(ordered)


def _card_record(
    card: Node,
    category: PropertyCategory,
    property_type: str,
    source_search_url: str,
    page: int,
    city: str | None,
    mappings: FieldMappings,
    now: datetime,
) -> ListingRecord | None:
    listing_id = str(card.attributes.get("data-uid") or "")
    if not _UID_PATTERNS[category].match(listing_id):
        return None
    anchor = card.css_first(".list-adress-v2 h3 a")
    href = anchor.attributes.get("href") if anchor is not None else None
    if anchor is None or not isinstance(href, str) or not href:
        return None
    canonical_url = _canonicalize(href)
    district, street = _address(anchor)
    floor, total_floors = _floors(_detail_value(card, "list-Floors-v2"))
    area = parse_decimal(_detail_value(card, "list-AreaOverall-v2"))
    heating = _detail_value(card, "list-HeatingType-v2")
    condition = _detail_value(card, "list-Equipment-v2")
    image_urls = _image_urls(card)
    thumbnail = card.css_first("img[data-src]")
    title = thumbnail.attributes.get("alt") if thumbnail is not None else None
    record_data: dict[str, Any] = {
        "scrape_timestamp_utc": now,
        "listing_id": listing_id,
        "listing_url": canonical_url,
        "canonical_url": canonical_url,
        "property_type": property_type,
        "record_source": "search",
        "city": city,
        "district": district,
        "street": street,
        "title_lt": redact_contact_details(clean_text(title)) if isinstance(title, str) else None,
        "price_eur": parse_decimal(_node_text(card, ".price .list-item-price-v2")),
        "price_per_sqm_eur": parse_decimal(_node_text(card, ".price .price-pm-v2")),
        "source_search_url": source_search_url,
        "source_page_number": page,
        "search_position": _search_position(href),
        "image_count": len(image_urls) or None,
        "image_urls": image_urls or None,
        "rooms": parse_integer(_detail_value(card, "list-RoomNum-v2")),
        "total_area_sqm": area,
        "floor": floor,
        "total_floors": total_floors,
        "construction_year": parse_integer(_detail_value(card, "list-BuildYear-v2")),
        "condition": mappings.normalize_category("condition", condition) if condition else None,
        "heating_type": (mappings.normalize_category("heating_type", heating) if heating else None),
    }
    if property_type == "apartment":
        record_data["apartment_total_area_sqm"] = area
    else:
        record_data["house_total_area_sqm"] = area
        record_data["number_of_floors"] = total_floors
    return ListingRecord.model_validate(record_data)


def parse_search_cards(
    html: str,
    category: PropertyCategory,
    source_search_url: str,
    page: int,
    city: str | None = None,
    mapping_path: Path | None = None,
) -> tuple[ListingRecord, ...]:
    """Build partial records from every genuine listing card on a search-result page.

    Cards whose ``data-uid`` does not match the category's identifier prefix are adverts
    or development-project promos and are skipped.
    """
    tree = HTMLParser(html)
    mappings = load_field_mappings(mapping_path)
    property_type = "apartment" if category == "apartments" else "house"
    now = datetime.now(UTC)
    records = (
        _card_record(card, category, property_type, source_search_url, page, city, mappings, now)
        for card in tree.css(_CARD_SELECTOR)
    )
    return tuple(record for record in records if record is not None)
