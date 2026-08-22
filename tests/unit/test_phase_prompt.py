"""Choosing between deepening and discovery at the start of a run."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from aruodas_scraper import cli
from aruodas_scraper.cities import load_city_registry
from aruodas_scraper.models import ListingRecord
from aruodas_scraper.pipelines.export import write_records

_REGISTRY = load_city_registry(Path("config/cities.yaml"))


def _record(listing_id: str, source: str) -> ListingRecord:
    return ListingRecord(
        scrape_timestamp_utc=datetime.now(UTC),
        listing_id=listing_id,
        listing_url=f"https://www.aruodas.lt/example-{listing_id}/",
        canonical_url=f"https://www.aruodas.lt/example-{listing_id}/",
        property_type="apartment",
        record_source=source,  # type: ignore[arg-type]
        source_search_url="https://www.aruodas.lt/butai/vilniuje/",
        source_page_number=1,
    )


def _export(output: Path, *records: ListingRecord) -> None:
    write_records(output / "apartments_vilnius.csv", list(records))


@pytest.mark.unit
def test_the_counts_offered_come_from_the_export(tmp_path: Path) -> None:
    """The prompt has to describe the real backlog, or it is asking about nothing."""
    _export(
        tmp_path,
        _record("1-1", "search"),
        _record("1-2", "search"),
        _record("1-3", "detail"),
    )

    total, card_only = cli._count_card_only(tmp_path, _REGISTRY, "vilnius", "apartments")

    assert (total, card_only) == (3, 2)


@pytest.mark.unit
def test_nothing_is_asked_when_there_is_no_backlog_to_deepen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With every listing already detailed there is only one thing the run can do."""
    _export(tmp_path, _record("1-3", "detail"))
    monkeypatch.setattr(
        cli.typer, "prompt", lambda *_args, **_kwargs: pytest.fail("should not have asked")
    )

    assert cli._choose_deepen(True, tmp_path, _REGISTRY, "vilnius", "apartments", 200) is True


@pytest.mark.unit
def test_a_missing_export_is_not_an_error_worth_prompting_about(tmp_path: Path) -> None:
    """A first run has nothing to deepen, so it should go straight to discovery."""
    total, card_only = cli._count_card_only(tmp_path, _REGISTRY, "vilnius", "apartments")

    assert (total, card_only) == (0, 0)


@pytest.mark.unit
@pytest.mark.parametrize(("answer", "expected"), [("1", True), ("2", False)])
def test_the_answer_decides_which_phase_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, expected: bool
) -> None:
    _export(tmp_path, _record("1-1", "search"), _record("1-3", "detail"))
    monkeypatch.setattr(cli.typer, "prompt", lambda *_args, **_kwargs: answer)

    chosen = cli._choose_deepen(True, tmp_path, _REGISTRY, "vilnius", "apartments", 200)

    assert chosen is expected


@pytest.mark.unit
def test_an_unreadable_export_does_not_break_the_prompt(tmp_path: Path) -> None:
    """The run reports a bad export properly moments later; a prompt is the wrong place to."""
    (tmp_path / "apartments_vilnius.csv").write_text("not,a,valid\nexport\n", encoding="utf-8")

    total, card_only = cli._count_card_only(tmp_path, _REGISTRY, "vilnius", "apartments")

    assert (total, card_only) == (0, 0)
