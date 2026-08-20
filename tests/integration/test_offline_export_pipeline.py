import csv
import json
import shutil
from pathlib import Path

import pytest

from aruodas_scraper.exceptions import CheckpointError
from aruodas_scraper.pipelines.all_properties import process_offline


@pytest.mark.integration
def test_offline_pipeline_writes_all_requested_artifacts(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "processed"
    checkpoint_path = tmp_path / "interim" / "checkpoint.json"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")
    shutil.copy("tests/fixtures/html/house_detail.html", input_directory / "house.html")

    summary = process_offline(
        input_directory=input_directory,
        output_directory=output_directory,
        city="vilnius",
        property_type="all",
        checkpoint_path=checkpoint_path,
    )

    expected = {
        "apartments_vilnius.csv",
        "houses_vilnius.csv",
        "scrape_summary.json",
        "data_quality_report.json",
        "failed_urls.csv",
        "unknown_fields.csv",
    }
    assert {path.name for path in output_directory.iterdir()} == expected
    assert summary.apartments_exported == 1
    assert summary.houses_exported == 1
    assert summary.failed == 0

    with (output_directory / "apartments_vilnius.csv").open(
        encoding="utf-8", newline=""
    ) as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert rows[0]["listing_id"] == "1-1234567"
    assert rows[0]["title_en"] == ""
    assert json.loads(rows[0]["raw_attributes_json"])["Naujas bandymo laukas"] == "Bandymo reikšmė"

    quality = json.loads((output_directory / "data_quality_report.json").read_text("utf-8"))
    assert quality["total_records"] == 2
    assert quality["duplicate_listing_ids"] == 0


@pytest.mark.integration
def test_offline_pipeline_resume_does_not_duplicate_records(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "processed"
    checkpoint_path = tmp_path / "interim" / "checkpoint.json"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")

    process_offline(input_directory, output_directory, "vilnius", "apartments", checkpoint_path)
    summary = process_offline(
        input_directory,
        output_directory,
        "vilnius",
        "apartments",
        checkpoint_path,
        resume=True,
    )

    assert summary.skipped_from_checkpoint == 1
    with (output_directory / "apartments_vilnius.csv").open(
        encoding="utf-8", newline=""
    ) as file_handle:
        assert len(list(csv.DictReader(file_handle))) == 1


@pytest.mark.integration
def test_resume_merges_new_records_with_existing_export(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "processed"
    checkpoint_path = tmp_path / "interim" / "checkpoint.json"
    input_directory.mkdir()
    first_html = Path("tests/fixtures/html/apartment_detail.html").read_text("utf-8")
    (input_directory / "first.html").write_text(first_html, encoding="utf-8")
    process_offline(input_directory, output_directory, "vilnius", "apartments", checkpoint_path)

    second_html = first_html.replace("1-1234567", "1-9999999").replace("148 500 €", "149 500 €")
    (input_directory / "second.html").write_text(second_html, encoding="utf-8")
    process_offline(
        input_directory,
        output_directory,
        "vilnius",
        "apartments",
        checkpoint_path,
        resume=True,
    )

    with (output_directory / "apartments_vilnius.csv").open(
        encoding="utf-8", newline=""
    ) as file_handle:
        identifiers = {row["listing_id"] for row in csv.DictReader(file_handle)}
    assert identifiers == {"1-1234567", "1-9999999"}


@pytest.mark.integration
def test_apartment_run_preserves_existing_house_export(tmp_path: Path) -> None:
    house_input_directory = tmp_path / "house-input"
    apartment_input_directory = tmp_path / "apartment-input"
    output_directory = tmp_path / "processed"
    house_input_directory.mkdir()
    apartment_input_directory.mkdir()
    shutil.copy("tests/fixtures/html/house_detail.html", house_input_directory / "house.html")
    process_offline(
        house_input_directory,
        output_directory,
        "vilnius",
        "houses",
        tmp_path / "house-checkpoint.json",
    )
    house_csv = output_directory / "houses_vilnius.csv"
    original_house_export = house_csv.read_bytes()

    shutil.copy(
        "tests/fixtures/html/apartment_detail.html",
        apartment_input_directory / "apartment.html",
    )
    process_offline(
        apartment_input_directory,
        output_directory,
        "vilnius",
        "apartments",
        tmp_path / "apartment-checkpoint.json",
    )

    assert house_csv.read_bytes() == original_house_export


@pytest.mark.integration
def test_export_failure_does_not_advance_checkpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")
    checkpoint_path = tmp_path / "checkpoint.json"

    def fail_export(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise OSError("injected export failure")

    monkeypatch.setattr("aruodas_scraper.pipelines.all_properties.write_records", fail_export)

    with pytest.raises(OSError, match="injected export failure"):
        process_offline(
            input_directory,
            tmp_path / "processed",
            "vilnius",
            "apartments",
            checkpoint_path,
        )

    assert not checkpoint_path.exists()


@pytest.mark.integration
def test_resume_rejects_a_different_output_directory(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")
    checkpoint_path = tmp_path / "checkpoint.json"
    process_offline(
        input_directory,
        tmp_path / "first-output",
        "vilnius",
        "apartments",
        checkpoint_path,
    )

    with pytest.raises(CheckpointError, match="different input, output, city, or property type"):
        process_offline(
            input_directory,
            tmp_path / "second-output",
            "vilnius",
            "apartments",
            checkpoint_path,
            resume=True,
        )


@pytest.mark.integration
def test_resume_rejects_missing_required_export(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "processed"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")
    checkpoint_path = tmp_path / "checkpoint.json"
    process_offline(
        input_directory,
        output_directory,
        "vilnius",
        "apartments",
        checkpoint_path,
    )
    (output_directory / "apartments_vilnius.csv").replace(
        tmp_path / "intentionally-moved-apartments.csv"
    )

    with pytest.raises(CheckpointError, match="required export is missing"):
        process_offline(
            input_directory,
            output_directory,
            "vilnius",
            "apartments",
            checkpoint_path,
            resume=True,
        )
