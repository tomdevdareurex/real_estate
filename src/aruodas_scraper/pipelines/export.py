"""Stable and atomic CSV/JSON artifact export."""

from __future__ import annotations

import csv
import json
import os
import secrets
from collections.abc import Iterable, Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from aruodas_scraper.constants import CSV_ENCODING
from aruodas_scraper.models import (
    FailedUrl,
    ListingRecord,
    OnlineScrapeSummary,
    ScrapeSummary,
    UnknownField,
)

_URL_FIELDS = ("listing_url", "canonical_url")
CSV_FIELDS = (
    tuple(field for field in ListingRecord.model_fields if field not in _URL_FIELDS) + _URL_FIELDS
)
_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _atomic_path(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")


def _csv_value(value: Any) -> str | int | float:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return _safe_csv_string(value)
    if isinstance(value, (int, float)):
        return value
    return _safe_csv_string(str(value))


def _safe_csv_string(value: str) -> str:
    """Neutralize spreadsheet formulas while preserving the original text."""
    return f"'{value}" if value.lstrip().startswith(_FORMULA_PREFIXES) else value


def read_records(path: Path) -> list[ListingRecord]:
    """Load a previously exported CSV for an incremental resume merge."""
    json_fields = {
        "all_features_json",
        "raw_attributes_json",
        "image_urls",
        "security_features",
        "additional_land_features",
        "diagnostic_warnings",
    }
    records: list[ListingRecord] = []
    with path.open(encoding=CSV_ENCODING, newline="") as file_handle:
        for row in csv.DictReader(file_handle):
            values: dict[str, Any] = {
                key: (None if value == "" else value) for key, value in row.items()
            }
            for field in json_fields:
                raw = values.get(field)
                if isinstance(raw, str):
                    values[field] = json.loads(raw)
            records.append(ListingRecord.model_validate(values))
    return records


def write_records(path: Path, records: Sequence[ListingRecord]) -> None:
    """Write listing records as UTF-8 CSV with a stable schema."""
    temporary = _atomic_path(path)
    with temporary.open("x", encoding=CSV_ENCODING, newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=CSV_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for record in records:
            writer.writerow({key: _csv_value(value) for key, value in record.model_dump().items()})
    os.replace(temporary, path)


def write_json(path: Path, value: BaseModel | Mapping[str, Any] | Sequence[Any]) -> None:
    """Write a JSON artifact atomically."""
    temporary = _atomic_path(path)
    data = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    with temporary.open("x", encoding="utf-8") as file_handle:
        file_handle.write(
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False) + "\n"
        )
    os.replace(temporary, path)


def write_failures(path: Path, failures: Sequence[FailedUrl]) -> None:
    """Write failed inputs even when no failures occurred."""
    fields = ("url", "stage", "error_type", "message")
    temporary = _atomic_path(path)
    with temporary.open("x", encoding=CSV_ENCODING, newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {key: _safe_csv_string(str(value)) for key, value in failure.model_dump().items()}
            for failure in failures
        )
    os.replace(temporary, path)


def write_unknown_fields(path: Path, unknown_fields: Iterable[UnknownField]) -> None:
    """Aggregate unknown labels and write samples for mapping maintenance."""
    grouped: dict[str, dict[str, Any]] = {}
    for item in unknown_fields:
        current = grouped.setdefault(
            item.label_lt,
            {
                "label_lt": item.label_lt,
                "occurrences": 0,
                "sample_value_lt": item.sample_value_lt,
                "sample_listing_url": item.listing_url,
            },
        )
        current["occurrences"] += 1
    fields = ("label_lt", "occurrences", "sample_value_lt", "sample_listing_url")
    temporary = _atomic_path(path)
    with temporary.open("x", encoding=CSV_ENCODING, newline="") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(
            {
                key: _safe_csv_string(str(value)) if isinstance(value, str) else value
                for key, value in grouped[label].items()
            }
            for label in sorted(grouped)
        )
    os.replace(temporary, path)


def write_summary(path: Path, summary: ScrapeSummary | OnlineScrapeSummary) -> None:
    """Write the run summary JSON artifact."""
    write_json(path, summary)
