"""Deduplication helpers for immutable discovery records."""

from collections.abc import Iterable

from aruodas_scraper.models import DiscoveryRecord


def deduplicate(records: Iterable[DiscoveryRecord]) -> tuple[DiscoveryRecord, ...]:
    """Keep the first record for each listing ID and canonical URL pair."""
    unique: dict[tuple[str, str], DiscoveryRecord] = {}
    for record in records:
        unique.setdefault((record.listing_id, record.canonical_url), record)
    return tuple(unique.values())
