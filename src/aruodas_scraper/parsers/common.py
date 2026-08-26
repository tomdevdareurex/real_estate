"""Shared, listing-scoped detail parsing."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from selectolax.parser import HTMLParser, Node

from aruodas_scraper.models import ListingRecord, UnknownField
from aruodas_scraper.normalization.dates import parse_iso_date
from aruodas_scraper.normalization.locations import parse_displayed_address
from aruodas_scraper.normalization.numbers import parse_decimal, parse_integer
from aruodas_scraper.normalization.privacy import redact_contact_details
from aruodas_scraper.normalization.text import clean_label, clean_text
from aruodas_scraper.normalization.translations import FieldMappings, load_field_mappings
from aruodas_scraper.normalization.units import parse_plot_area
from aruodas_scraper.parsers.coordinates import extract_coordinates
from aruodas_scraper.parsers.engagement import extract_engagement
from aruodas_scraper.parsers.structured_data import extract_json_ld

# 1/2 are sale apartments/houses, 4/5 their rental counterparts.
_LISTING_ID = re.compile(r"([1245]-\d+)(?:/)?$")
_DATE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def _node_text(scope: Node, selector: str) -> str | None:
    node = scope.css_first(selector)
    return clean_text(node.text(separator=" ")) if node else None


def _canonical_url(tree: HTMLParser) -> str:
    canonical = tree.css_first('link[rel="canonical"]')
    if canonical is None or not canonical.attributes.get("href"):
        raise ValueError("Listing page does not contain a canonical URL.")
    href = str(canonical.attributes["href"])
    parts = urlsplit(href)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def _attribute_pairs(scope: Node) -> dict[str, str]:
    result: dict[str, str] = {}
    for term in scope.css("dl.obj-details dt"):
        value_node = term.next
        while value_node is not None and value_node.tag != "dd":
            value_node = value_node.next
        if value_node is not None:
            label = clean_label(term.text(separator=" "))
            value = clean_text(value_node.text(separator=" "))
            if label and value:
                result[label] = redact_contact_details(value) or ""
    return result


def _feature_values(
    scope: Node, mappings: FieldMappings
) -> tuple[dict[str, bool], tuple[str, ...]]:
    normalized: dict[str, bool] = {}
    raw: list[str] = []
    nodes = scope.css("section.features span") or scope.css(".features-list-in-object-item")
    for node in nodes:
        value = clean_text(node.text(separator=" "))
        if not value:
            continue
        raw.append(value)
        mapped = mappings.features.get(value)
        if mapped:
            normalized[mapped] = True
    return normalized, tuple(raw)


def _meta_content(tree: HTMLParser, property_name: str) -> str | None:
    node = tree.css_first(f'meta[property="{property_name}"]')
    if node is None:
        return None
    content = node.attributes.get("content")
    return clean_text(content) if isinstance(content, str) else None


def _object_info_stats(tree: HTMLParser) -> dict[str, str]:
    """Read the label/value stats block used by the current site markup."""
    container = tree.css_first(".object-info-stats-container")
    if container is None:
        return {}
    stats: dict[str, str] = {}
    for row in container.css(".object-info-stats-row"):
        label_node = row.css_first(".object-info-stats-label")
        value_node = row.css_first(".object-info-stats-value")
        if label_node is None or value_node is None:
            continue
        label = clean_text(label_node.text(separator=" "))
        value = clean_text(value_node.text(separator=" "))
        if label and value:
            stats[label] = value
    return stats


def _normalized_attributes(
    raw: dict[str, str], mappings: FieldMappings, property_type: str
) -> tuple[dict[str, Any], set[str]]:
    values: dict[str, Any] = {}
    known_labels: set[str] = set()
    integer_fields = {
        "rooms",
        "bedrooms",
        "bathrooms",
        "floor",
        "total_floors",
        "construction_year",
        "renovation_year",
    }
    decimal_fields = {
        "total_area_sqm",
        "living_area_sqm",
        "usable_area_sqm",
        "basement_area_sqm",
        "garage_area_sqm",
        "completion_percent",
    }
    for label, raw_value in raw.items():
        field = mappings.fields.get(label)
        if field is None:
            continue
        known_labels.add(label)
        if field == "plot_area":
            plot = parse_plot_area(raw_value)
            values.update(
                plot_area_original=plot.original,
                plot_area_unit=plot.unit,
                plot_area_sqm=plot.square_metres,
                plot_area_ares=plot.ares,
            )
        elif field in integer_fields:
            values[field] = parse_integer(raw_value)
        elif field in decimal_fields:
            values[field] = parse_decimal(raw_value)
        elif field in mappings.categories:
            values[field] = mappings.normalize_category(field, raw_value)
        else:
            values[field] = raw_value
    area = values.get("total_area_sqm")
    if property_type == "apartment":
        values["apartment_total_area_sqm"] = area
    else:
        values["house_total_area_sqm"] = area
        values["number_of_floors"] = values.get("total_floors")
    return values, known_labels


def parse_listing(
    html: str,
    property_type: str,
    source_search_url: str,
    page: int,
    mapping_path: Path | None = None,
    listing_type: str = "sale",
) -> tuple[ListingRecord, tuple[UnknownField, ...]]:
    """Parse one apartment or house from the primary listing container."""
    tree = HTMLParser(html)
    scope = (
        tree.css_first("#listing-detail")
        or tree.css_first("main")
        or tree.css_first(".main-content-in-object")
        or tree.css_first(".obj-cont")
    )
    if scope is None:
        raise ValueError("Listing page does not contain a primary listing container.")
    canonical_url = _canonical_url(tree)
    identifier_match = _LISTING_ID.search(urlsplit(canonical_url).path.rstrip("/"))
    if identifier_match is None:
        raise ValueError("Canonical URL does not contain a supported listing ID.")
    listing_id = identifier_match.group(1)
    mappings = load_field_mappings(mapping_path)
    raw_attributes = _attribute_pairs(scope)
    normalized, known_labels = _normalized_attributes(raw_attributes, mappings, property_type)
    features, raw_features = _feature_values(scope, mappings)
    scope_views, scope_saved = extract_engagement(scope)
    coordinate, precision = extract_coordinates(tree, scope)
    address = parse_displayed_address(_node_text(scope, "h1"))
    structured = extract_json_ld(tree)
    primary_structured = structured[0] if structured else {}
    title_meta = tree.css_first('meta[property="og:title"]')
    title_lt = (
        title_meta.attributes.get("content") if title_meta else primary_structured.get("name")
    )
    description_lt = (
        _node_text(scope, "#collapsedText")
        or _node_text(scope, ".obj-comment")
        or _node_text(scope, ".description")
        or _meta_content(tree, "og:description")
        or primary_structured.get("description")
        or None
    )
    offers = primary_structured.get("Offers") or primary_structured.get("offers")
    offer_price = offers.get("price") if isinstance(offers, dict) else None
    price_eur = parse_decimal(str(offer_price)) if offer_price else None
    if price_eur is None:
        price_eur = parse_decimal(_node_text(scope, ".price"))
    if price_eur is None:
        price_eur = parse_decimal(_node_text(scope, ".price-main .price-eur"))
    price_per_sqm_eur = parse_decimal(_node_text(scope, ".price-per-sqm"))
    if price_per_sqm_eur is None:
        price_per_sqm_eur = parse_decimal(_node_text(scope, ".price-main .price-per"))
    stats = _object_info_stats(tree)
    created = parse_iso_date(stats.get("Įdėtas"))
    updated = parse_iso_date(stats.get("Redaguotas"))
    if created is None and updated is None:
        stats_text = _node_text(scope, ".listing-stats") or ""
        dates = _DATE.findall(stats_text)
        created = parse_iso_date(dates[0]) if dates else None
        updated = parse_iso_date(dates[1]) if len(dates) > 1 else None
    tree_views = parse_integer(stats.get("Peržiūrėjo"))
    tree_saved = parse_integer(stats.get("Įsiminė"))
    views = tree_views if tree_views is not None else scope_views
    saved = tree_saved if tree_saved is not None else scope_saved
    now = datetime.now(UTC)
    warnings = (coordinate.warning,) if coordinate.warning else ()
    security = (
        tuple(
            raw_value
            for raw_value in raw_features
            if mappings.features.get(raw_value, "").startswith(("alarm", "security", "fenced"))
        )
        or None
    )
    record_data: dict[str, Any] = {
        "scrape_timestamp_utc": now,
        "listing_id": listing_id,
        "listing_url": canonical_url,
        "canonical_url": canonical_url,
        "property_type": property_type,
        "listing_type": listing_type,
        "municipality": address.city,
        "city": address.city,
        "neighbourhood": address.neighbourhood,
        "local_area": address.neighbourhood,
        "street": address.street,
        "full_address": address.full_address,
        "title_lt": (
            redact_contact_details(clean_text(title_lt)) if isinstance(title_lt, str) else None
        ),
        "description_lt": (
            redact_contact_details(clean_text(description_lt))
            if isinstance(description_lt, str)
            else None
        ),
        "price_eur": price_eur,
        "price_per_sqm_eur": price_per_sqm_eur,
        "listing_created_date": created,
        "listing_updated_date": updated,
        "listing_age_days": (now.date() - created).days if created else None,
        "source_search_url": source_search_url,
        "source_page_number": page,
        "views_count": views,
        "saved_by_users_count": saved,
        "latitude": coordinate.latitude,
        "longitude": coordinate.longitude,
        "coordinate_source": "embedded_script" if coordinate.latitude is not None else None,
        "coordinate_precision": precision,
        "security_features": security,
        "all_features_json": features,
        "raw_attributes_json": raw_attributes,
        "diagnostic_warnings": warnings,
        **normalized,
        **{
            key: True
            for key in ("garage", "balcony", "terrace", "basement", "storage", "lift")
            if features.get(key)
        },
    }
    record = ListingRecord.model_validate(record_data)
    unknown = tuple(
        UnknownField(
            label_lt=label,
            sample_value_lt=value,
            listing_id=listing_id,
            listing_url=canonical_url,
        )
        for label, value in raw_attributes.items()
        if label not in known_labels
    )
    return record, unknown
