"""Offline orchestration for apartments and houses."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from selectolax.parser import HTMLParser

from aruodas_scraper.constants import MAX_HTML_FILE_BYTES, MAX_HTML_FILES
from aruodas_scraper.exceptions import CheckpointError
from aruodas_scraper.models import FailedUrl, ListingRecord, ScrapeSummary, UnknownField
from aruodas_scraper.parsers.apartment import parse_apartment
from aruodas_scraper.parsers.house import parse_house
from aruodas_scraper.pipelines.checkpoints import (
    CheckpointContext,
    load_checkpoint,
    save_checkpoint,
)
from aruodas_scraper.pipelines.export import (
    read_records,
    write_failures,
    write_json,
    write_records,
    write_summary,
    write_unknown_fields,
)
from aruodas_scraper.validation.quality_report import build_quality_report


def _classify(html: str) -> str | None:
    tree = HTMLParser(html)
    canonical = tree.css_first('link[rel="canonical"]')
    href = str(canonical.attributes.get("href") or "") if canonical else ""
    if "1-" in href and "/butai-" in href:
        return "apartments"
    if "2-" in href and "/namai-" in href:
        return "houses"
    return None


def process_offline(
    input_directory: Path,
    output_directory: Path,
    city: str,
    property_type: str,
    checkpoint_path: Path,
    resume: bool = False,
    refresh: bool = False,
) -> ScrapeSummary:
    """Parse local detail pages and emit all requested artifacts."""
    if re.fullmatch(r"[a-z][a-z0-9_-]*", city) is None:
        raise ValueError("City must be a lowercase configuration key.")
    started = datetime.now(UTC)
    output_directory.mkdir(parents=True, exist_ok=True)
    context = CheckpointContext(
        str(input_directory.resolve()),
        str(output_directory.resolve()),
        city,
        property_type,
    )
    processed = load_checkpoint(checkpoint_path, context) if resume and not refresh else {}
    apartment_path = output_directory / f"apartments_{city}.csv"
    house_path = output_directory / f"houses_{city}.csv"
    required_exports = []
    if property_type in {"apartments", "all"}:
        required_exports.append(apartment_path)
    if property_type in {"houses", "all"}:
        required_exports.append(house_path)
    missing_exports = [path for path in required_exports if processed and not path.is_file()]
    if missing_exports:
        missing_names = ", ".join(path.name for path in missing_exports)
        raise CheckpointError(
            f"Resume checkpoint exists but a required export is missing: {missing_names}. "
            "Use --refresh to rebuild outputs."
        )
    updated_processed = dict(processed)
    apartments: list[ListingRecord] = []
    houses: list[ListingRecord] = []
    unknown_fields: list[UnknownField] = []
    failures: list[FailedUrl] = []
    skipped = 0
    apartment_files_seen = 0
    house_files_seen = 0

    html_paths = sorted(input_directory.glob("*.html"))
    if len(html_paths) > MAX_HTML_FILES:
        raise ValueError(f"Input directory exceeds the {MAX_HTML_FILES}-file safety limit.")
    for path in html_paths:
        if path.is_symlink():
            failures.append(
                FailedUrl(
                    url=str(path),
                    stage="read_input",
                    error_type="SymlinkRejected",
                    message="Symbolic-link HTML inputs are not accepted.",
                )
            )
            continue
        if path.stat().st_size > MAX_HTML_FILE_BYTES:
            failures.append(
                FailedUrl(
                    url=str(path),
                    stage="read_input",
                    error_type="FileTooLarge",
                    message=f"HTML input exceeds {MAX_HTML_FILE_BYTES} bytes.",
                )
            )
            continue
        content_digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if processed.get(path.name) == content_digest:
            skipped += 1
            continue
        try:
            html = path.read_text(encoding="utf-8")
            category = _classify(html)
            if category is None or (property_type != "all" and category != property_type):
                continue
            if category == "apartments":
                apartment_files_seen += 1
                record, unknown = parse_apartment(html, f"offline://{path.name}", 1)
                apartments.append(record)
            else:
                house_files_seen += 1
                record, unknown = parse_house(html, f"offline://{path.name}", 1)
                houses.append(record)
            unknown_fields.extend(unknown)
            updated_processed[path.name] = content_digest
        except (OSError, UnicodeError, ValueError) as error:
            failures.append(
                FailedUrl(
                    url=str(path),
                    stage="parse_detail",
                    error_type=type(error).__name__,
                    message=str(error),
                )
            )

    existing_apartments = read_records(apartment_path) if resume and apartment_path.exists() else []
    existing_houses = read_records(house_path) if resume and house_path.exists() else []
    final_apartments = list(
        {record.listing_id: record for record in [*existing_apartments, *apartments]}.values()
    )
    final_houses = list(
        {record.listing_id: record for record in [*existing_houses, *houses]}.values()
    )
    if property_type in {"apartments", "all"}:
        write_records(apartment_path, final_apartments)
    if property_type in {"houses", "all"}:
        write_records(house_path, final_houses)

    all_records = [
        *(final_apartments if property_type in {"apartments", "all"} else []),
        *(final_houses if property_type in {"houses", "all"} else []),
    ]
    quality = build_quality_report(all_records)
    summary = ScrapeSummary(
        city=city,
        started_at_utc=started,
        completed_at_utc=datetime.now(UTC),
        apartment_files_seen=apartment_files_seen,
        house_files_seen=house_files_seen,
        apartments_exported=len(final_apartments),
        houses_exported=len(final_houses),
        failed=len(failures),
        unknown_labels=len({item.label_lt for item in unknown_fields}),
        skipped_from_checkpoint=skipped,
    )
    write_summary(output_directory / "scrape_summary.json", summary)
    write_json(output_directory / "data_quality_report.json", quality)
    write_failures(output_directory / "failed_urls.csv", failures)
    write_unknown_fields(output_directory / "unknown_fields.csv", unknown_fields)
    save_checkpoint(checkpoint_path, context, updated_processed)
    return summary
