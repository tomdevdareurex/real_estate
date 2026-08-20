"""Dataset-level completeness and duplicate reporting."""

from collections import Counter
from collections.abc import Sequence
from typing import Any

from aruodas_scraper.models import ListingRecord


def build_quality_report(records: Sequence[ListingRecord]) -> dict[str, Any]:
    """Build deterministic missingness, duplicate, and warning metrics."""
    identifiers = [record.listing_id for record in records]
    duplicate_count = sum(count - 1 for count in Counter(identifiers).values() if count > 1)
    missingness = {
        field: sum(getattr(record, field) is None for record in records)
        for field in ListingRecord.model_fields
    }
    return {
        "total_records": len(records),
        "duplicate_listing_ids": duplicate_count,
        "records_with_warnings": sum(bool(record.diagnostic_warnings) for record in records),
        "missing_values_by_field": missingness,
        "records_by_property_type": dict(
            sorted(Counter(record.property_type for record in records).items())
        ),
    }
