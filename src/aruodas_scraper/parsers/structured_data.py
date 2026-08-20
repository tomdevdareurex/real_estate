"""Safe extraction of public JSON-LD objects."""

import json
from typing import Any

from selectolax.parser import HTMLParser

from aruodas_scraper.constants import MAX_JSON_LD_BYTES


def extract_json_ld(tree: HTMLParser) -> tuple[dict[str, Any], ...]:
    """Return valid JSON-LD mappings while ignoring malformed scripts."""
    results: list[dict[str, Any]] = []
    for node in tree.css('script[type="application/ld+json"]'):
        if len(node.text().encode("utf-8")) > MAX_JSON_LD_BYTES:
            continue
        try:
            value = json.loads(node.text())
        except (json.JSONDecodeError, TypeError):
            continue
        candidates = value if isinstance(value, list) else [value]
        results.extend(item for item in candidates if isinstance(item, dict))
    return tuple(results)
