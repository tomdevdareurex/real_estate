from pathlib import Path

import pytest

from aruodas_scraper.parsers.search_card import parse_search_cards

_FIXTURE = Path("tests/fixtures/html/search_cards_apartments.html")


def _apartment_records() -> tuple:
    return parse_search_cards(
        _FIXTURE.read_text(encoding="utf-8"),
        category="apartments",
        source_search_url="https://www.aruodas.lt/butai/vilniuje/",
        page=1,
        city="Vilnius",
    )


@pytest.mark.unit
def test_parses_every_field_a_card_carries() -> None:
    record = _apartment_records()[0]

    assert record.listing_id == "1-1234567"
    assert record.record_source == "search"
    assert record.property_type == "apartment"
    assert record.canonical_url == (
        "https://www.aruodas.lt/butai-vilniuje-zirmunuose-testu-g-butas-1-1234567/"
    )
    assert record.city == "Vilnius"
    assert record.district == "Žirmūnai"
    assert record.street == "Testų g."
    assert record.search_position == 1
    assert record.price_eur == 123400.0
    assert record.price_per_sqm_eur == 1890.0
    assert record.rooms == 3
    assert record.total_area_sqm == 65.3
    assert record.apartment_total_area_sqm == 65.3
    assert record.floor == 2
    assert record.total_floors == 4
    assert record.construction_year == 2011
    assert record.condition == "fully_finished"
    assert record.heating_type == "Centrinis kolektorinis"
    assert record.source_page_number == 1


@pytest.mark.unit
def test_skips_other_categories_promos_and_linkless_cards() -> None:
    assert [record.listing_id for record in _apartment_records()] == ["1-1234567", "1-7654321"]


@pytest.mark.unit
def test_missing_card_details_stay_null_rather_than_zero() -> None:
    record = _apartment_records()[1]

    assert record.price_eur == 75000.0
    assert record.price_per_sqm_eur is None
    assert record.floor is None
    assert record.total_floors is None
    assert record.construction_year is None
    assert record.image_urls is None
    assert record.image_count is None
    assert record.condition == "unfinished"


@pytest.mark.unit
def test_collects_the_thumbnail_and_its_extra_images() -> None:
    record = _apartment_records()[0]

    assert record.image_urls == (
        "https://img.example/object_1/first.jpg",
        "https://img.example/object_1/second.jpg",
        "https://img.example/object_1/third.jpg",
    )
    assert record.image_count == 3
    assert record.title_lt == "Žirmūnai, Testų g., 3 kambarių butas"


@pytest.mark.unit
def test_house_category_keeps_only_house_identifiers() -> None:
    records = parse_search_cards(
        _FIXTURE.read_text(encoding="utf-8"),
        category="houses",
        source_search_url="https://www.aruodas.lt/namai/vilniuje/",
        page=2,
    )

    assert [record.listing_id for record in records] == ["2-1111111"]
    assert records[0].property_type == "house"
    assert records[0].source_page_number == 2
