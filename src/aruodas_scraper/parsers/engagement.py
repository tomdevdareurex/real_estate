"""Engagement metrics extracted only from the primary listing scope."""

import re

from selectolax.parser import Node

from aruodas_scraper.normalization.numbers import parse_integer

_SAVED = re.compile(r"Įsiminė\s+([\d\s\u00a0]+)")
_VIEWS = re.compile(r"Peržiūrėjo\s+([\d\s\u00a0]+)(?:/\d+)?")


def extract_engagement(scope: Node) -> tuple[int | None, int | None]:
    """Return total views and saved count from the genuine listing container."""
    stats = scope.css_first(".listing-stats")
    text = stats.text(separator=" ") if stats else ""
    saved_match = _SAVED.search(text)
    views_match = _VIEWS.search(text)
    saved = parse_integer(saved_match.group(1)) if saved_match else None
    views = parse_integer(views_match.group(1)) if views_match else None
    return views, saved
