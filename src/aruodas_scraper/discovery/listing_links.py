"""Discover genuine Aruodas listing URLs from saved result pages."""

import re
from urllib.parse import parse_qs, urljoin, urlsplit, urlunsplit

from selectolax.parser import HTMLParser

from aruodas_scraper.models import DiscoveryRecord, DiscoveryResult

# Detail URLs join the category segment to the slug with a hyphen (`/butu-nuoma-vilniuje-...`),
# while the search and pagination URLs below use a slash (`/butu-nuoma/vilniuje/`), so a
# pagination link can never satisfy a listing pattern. The rent patterns need no counterpart to
# the `patalpa-` alternation: that exists because sale apartments share a result surface with
# commercial premises, which the rental sections do not.
_PATTERNS = {
    "apartments": re.compile(r"/(?:butai|patalpa-[^/]+)-[^?#]*-(1-\d+)/?$"),
    "houses": re.compile(r"/namai-[^?#]*-(2-\d+)/?$"),
    "apartments_rent": re.compile(r"/butu-nuoma-[^?#]*-(4-\d+)/?$"),
    "houses_rent": re.compile(r"/namu-nuoma-[^?#]*-(5-\d+)/?$"),
}
# Aruodas puts the search filters between the category and the page number, so the real link is
# `/butai/vilniuje/puslapis/2/` rather than `/butai/puslapis/2/`. Matching only the unfiltered
# shape left `next_page_url` empty on every live page and silently capped each crawl at page 1.
# The category segment is pinned per property type so a cross-category link in the page chrome
# cannot hijack the walk.
_PAGINATION = {
    "apartments": re.compile(r"/butai/(?:[^/]+/)*puslapis/(\d+)/?$"),
    "houses": re.compile(r"/namai/(?:[^/]+/)*puslapis/(\d+)/?$"),
    "apartments_rent": re.compile(r"/butu-nuoma/(?:[^/]+/)*puslapis/(\d+)/?$"),
    "houses_rent": re.compile(r"/namu-nuoma/(?:[^/]+/)*puslapis/(\d+)/?$"),
}


def _canonicalize(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))


def discover_listing_links(
    html: str,
    property_type: str,
    source_search_url: str,
    source_page_number: int,
) -> DiscoveryResult:
    """Extract, classify, and deduplicate genuine listing links."""
    pattern = _PATTERNS[property_type]
    pagination = _PAGINATION[property_type]
    tree = HTMLParser(html)
    records: list[DiscoveryRecord] = []
    seen_ids: set[str] = set()
    next_page_url: str | None = None
    base_url = "https://www.aruodas.lt/"

    for anchor in tree.css("a[href]"):
        # `a[href]` also matches a valueless attribute, for which selectolax yields None.
        href = anchor.attributes.get("href") or ""
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.netloc not in {"www.aruodas.lt", "aruodas.lt"}:
            continue
        listing_match = pattern.search(parts.path)
        if listing_match:
            listing_id = listing_match.group(1)
            if listing_id in seen_ids:
                continue
            seen_ids.add(listing_id)
            query = parse_qs(parts.query)
            position_text = query.get("search_pos", [None])[0]
            position = int(position_text) if position_text and position_text.isdigit() else None
            canonical = _canonicalize(absolute)
            records.append(
                DiscoveryRecord(
                    listing_id=listing_id,
                    listing_url=absolute,
                    canonical_url=canonical,
                    source_search_url=source_search_url,
                    source_page_number=source_page_number,
                    search_position=position,
                )
            )
            continue
        page_match = pagination.search(parts.path)
        if page_match and int(page_match.group(1)) > source_page_number:
            candidate = _canonicalize(absolute)
            if next_page_url is None or int(page_match.group(1)) == source_page_number + 1:
                next_page_url = candidate

    return DiscoveryResult(listings=tuple(records), next_page_url=next_page_url)
