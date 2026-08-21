"""CSV artifacts must open correctly in Excel without corrupting the merge that reads them."""

from __future__ import annotations

import csv
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aruodas_scraper.constants import CSV_ENCODING
from aruodas_scraper.models import FailedUrl, ListingRecord, UnknownField
from aruodas_scraper.pipelines.export import (
    read_records,
    write_failures,
    write_json,
    write_records,
    write_unknown_fields,
)
from aruodas_scraper.validation.records import validate_csv

_BOM = b"\xef\xbb\xbf"

# Every Lithuanian diacritic, so a codec that mangles any one of them fails loudly.
_LITHUANIAN = "Visorių g., 2 kambarių butas: ąčęėįšųūž ĄČĘĖĮŠŲŪŽ"


def _record(**overrides: object) -> ListingRecord:
    values: dict[str, object] = {
        "scrape_timestamp_utc": datetime.now(UTC),
        "listing_id": "1-3685906",
        "listing_url": "https://www.aruodas.lt/example-1-3685906/",
        "canonical_url": "https://www.aruodas.lt/example-1-3685906/",
        "property_type": "apartment",
        "source_search_url": "https://www.aruodas.lt/butai-vilniuje/",
        "source_page_number": 1,
        "title_lt": _LITHUANIAN,
    }
    values.update(overrides)
    return ListingRecord(**values)  # type: ignore[arg-type]


@pytest.mark.unit
def test_the_listing_export_opens_with_a_byte_order_mark(tmp_path: Path) -> None:
    """Without it Excel decodes the file as ANSI and shows mojibake for every diacritic."""
    output = tmp_path / "apartments_vilnius.csv"

    write_records(output, [_record()])

    assert output.read_bytes().startswith(_BOM)


@pytest.mark.unit
def test_lithuanian_text_survives_a_write_then_read(tmp_path: Path) -> None:
    output = tmp_path / "apartments_vilnius.csv"

    write_records(output, [_record()])

    with output.open(encoding=CSV_ENCODING, newline="") as file_handle:
        row = next(csv.DictReader(file_handle))
    assert row["title_lt"] == _LITHUANIAN


@pytest.mark.unit
def test_reading_an_export_back_does_not_see_the_mark_as_part_of_a_column_name(
    tmp_path: Path,
) -> None:
    """This is the trap the BOM sets, and it is silent.

    Online runs are additive: the export is read back and merged by listing_id. Read with
    plain utf-8 the BOM stays glued to the first header, so `scrape_timestamp_utc` arrives
    as a field nothing recognises - every row would look new and the merge would duplicate
    rather than update.
    """
    output = tmp_path / "apartments_vilnius.csv"
    write_records(output, [_record()])

    with output.open(encoding=CSV_ENCODING, newline="") as file_handle:
        fieldnames = csv.DictReader(file_handle).fieldnames or []

    assert fieldnames[0] == "scrape_timestamp_utc"
    assert not any(name.startswith("﻿") for name in fieldnames)


@pytest.mark.unit
def test_a_written_export_round_trips_through_the_merge_reader(tmp_path: Path) -> None:
    """`read_records` is what a repeat run merges against, so it must accept what we write."""
    output = tmp_path / "apartments_vilnius.csv"
    write_records(output, [_record()])

    loaded = read_records(output)

    assert len(loaded) == 1
    assert loaded[0].listing_id == "1-3685906"
    assert loaded[0].title_lt == _LITHUANIAN


@pytest.mark.unit
def test_an_export_written_before_this_change_still_loads(tmp_path: Path) -> None:
    """utf-8-sig tolerates a missing BOM, so existing exports must not need re-generating."""
    output = tmp_path / "apartments_vilnius.csv"
    write_records(output, [_record()])
    without_bom = output.read_bytes().removeprefix(_BOM)
    output.write_bytes(without_bom)

    loaded = read_records(output)

    assert loaded[0].title_lt == _LITHUANIAN


@pytest.mark.unit
def test_validation_reads_the_columns_it_requires(tmp_path: Path) -> None:
    """`validate` checks required columns by name, which a stuck BOM would hide."""
    output = tmp_path / "apartments_vilnius.csv"
    write_records(output, [_record()])

    result = validate_csv(output)

    assert result.total_records == 1
    assert result.duplicate_listing_ids == 0


@pytest.mark.unit
def test_the_failure_and_unknown_field_reports_also_open_in_excel(tmp_path: Path) -> None:
    """Both are opened in a spreadsheet as often as the export itself."""
    failures = tmp_path / "failed_urls.csv"
    unknown = tmp_path / "unknown_fields.csv"

    write_failures(
        failures,
        [
            FailedUrl(
                url="https://www.aruodas.lt/example-1-3685906/",
                stage="detail",
                error_type="BlockedError",
                message=_LITHUANIAN,
            )
        ],
    )
    write_unknown_fields(
        unknown,
        [
            UnknownField(
                listing_id="1-3685906",
                label_lt=_LITHUANIAN,
                sample_value_lt=_LITHUANIAN,
                listing_url="https://www.aruodas.lt/example-1-3685906/",
            )
        ],
    )

    assert failures.read_bytes().startswith(_BOM)
    assert unknown.read_bytes().startswith(_BOM)


@pytest.mark.unit
def test_json_artifacts_are_left_without_a_mark(tmp_path: Path) -> None:
    """A BOM is invalid to many JSON parsers, and nothing opens these in a spreadsheet."""
    output = tmp_path / "data_quality_report.json"

    write_json(output, {"note": _LITHUANIAN})

    assert not output.read_bytes().startswith(_BOM)
    # json.loads rejects a leading BOM outright, so this asserts the file stays consumable.
    assert json.loads(output.read_text(encoding="utf-8"))["note"] == _LITHUANIAN
