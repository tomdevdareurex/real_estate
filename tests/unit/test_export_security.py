import csv
from datetime import UTC, datetime
from pathlib import Path

import pytest

from aruodas_scraper.models import ListingRecord
from aruodas_scraper.pipelines.export import write_records


@pytest.mark.unit
@pytest.mark.parametrize("dangerous", ["=1+1", "+cmd", "-2+3", "@SUM(A1:A2)", "  =1+1"])
def test_csv_export_neutralizes_formula_cells(tmp_path: Path, dangerous: str) -> None:
    record = ListingRecord(
        scrape_timestamp_utc=datetime.now(UTC),
        listing_id="1-1234567",
        listing_url="https://www.aruodas.lt/example-1-1234567/",
        canonical_url="https://www.aruodas.lt/example-1-1234567/",
        property_type="apartment",
        source_search_url="offline://fixture",
        source_page_number=1,
        title_lt=dangerous,
    )
    output = tmp_path / "records.csv"

    write_records(output, [record])

    with output.open(encoding="utf-8", newline="") as file_handle:
        row = next(csv.DictReader(file_handle))
    assert row["title_lt"].lstrip().startswith("'")
