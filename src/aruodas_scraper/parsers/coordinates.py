"""Coordinate extraction from public listing HTML and embedded scripts."""

import re

from selectolax.parser import HTMLParser, Node

from aruodas_scraper.validation.coordinates import CoordinateValidation, validate_coordinates

_LATITUDE = re.compile(r"[\"']?lat[\"']?\s*[:=]\s*([+-]?\d+(?:\.\d+)?)", re.IGNORECASE)
_LONGITUDE = re.compile(
    r"[\"']?(?:lng|lon|longitude)[\"']?\s*[:=]\s*([+-]?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_MAP_MARKERS = ("mapConfig", "locationData", "listingMap")
# Matches `const coordinates = '54.7,25.3'` and `const [lat, lng] = '54.7,25.3'.split(',')`.
_COORDINATE_PAIR = re.compile(
    r"(?:coordinates|\[\s*lat\s*,\s*lng\s*\])\s*=\s*"
    r"['\"]([+-]?\d+\.\d+)\s*,\s*([+-]?\d+\.\d+)['\"]"
)


def extract_coordinates(tree: HTMLParser, scope: Node) -> tuple[CoordinateValidation, str | None]:
    """Extract and validate an explicit coordinate pair from scripts."""
    source: str | None = "embedded_script"
    latitude: float | None = None
    longitude: float | None = None
    for script in tree.css("script"):
        text = script.text()
        marker_positions = [text.find(marker) for marker in _MAP_MARKERS if marker in text]
        if not marker_positions:
            continue
        listing_map_text = text[min(marker_positions) :]
        latitude_match = _LATITUDE.search(listing_map_text)
        longitude_match = _LONGITUDE.search(listing_map_text)
        if latitude_match and longitude_match:
            latitude = float(latitude_match.group(1))
            longitude = float(longitude_match.group(1))
            break
    if latitude is None or longitude is None:
        for script in tree.css("script"):
            pair_match = _COORDINATE_PAIR.search(script.text())
            if pair_match:
                latitude = float(pair_match.group(1))
                longitude = float(pair_match.group(2))
                break
    validation = validate_coordinates(latitude, longitude)
    if validation.latitude is None:
        source = None
    precision = scope.attributes.get("data-coordinate-precision")
    if precision is None and "Taškas žemėlapyje netikslus" in scope.text(separator=" "):
        precision = "approximate"
    return validation, precision
