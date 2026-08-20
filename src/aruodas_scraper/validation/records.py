"""Validation for exported listing CSV files."""

import csv
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CsvValidationResult:
    """Result of validating one exported CSV."""

    total_records: int
    duplicate_listing_ids: int


def validate_csv(path: Path) -> CsvValidationResult:
    """Validate required columns, identifiers, and duplicate listing IDs."""
    with path.open(encoding="utf-8", newline="") as file_handle:
        reader = csv.DictReader(file_handle)
        required = {"listing_id", "canonical_url", "property_type"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise ValueError(f"CSV is missing required columns: {', '.join(sorted(missing))}")
        rows = list(reader)
    identifiers = [row["listing_id"] for row in rows]
    duplicates = len(identifiers) - len(set(identifiers))
    if any(not identifier for identifier in identifiers):
        raise ValueError("CSV contains a record without listing_id.")
    return CsvValidationResult(total_records=len(rows), duplicate_listing_ids=duplicates)
