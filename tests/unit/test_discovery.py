from pathlib import Path

import pytest

from aruodas_scraper.discovery.listing_links import discover_listing_links


@pytest.mark.unit
def test_discovery_keeps_only_genuine_apartment_links() -> None:
    html = Path("tests/fixtures/html/search_apartments.html").read_text(encoding="utf-8")

    result = discover_listing_links(
        html,
        property_type="apartments",
        source_search_url="https://www.aruodas.lt/butai/vilniuje/",
        source_page_number=1,
    )

    assert [item.listing_id for item in result.listings] == ["1-1234567", "1-7654321"]
    assert [item.search_position for item in result.listings] == [1, 2]
    assert result.next_page_url == "https://www.aruodas.lt/butai/puslapis/2/"


@pytest.mark.unit
def test_discovery_deduplicates_listing_ids() -> None:
    html = """
    <a href='/butai-vilniuje-test-1-1234567/?search_pos=1'>one</a>
    <a href='/butai-vilniuje-test-1-1234567/?search_pos=2'>duplicate</a>
    """

    result = discover_listing_links(html, "apartments", "https://www.aruodas.lt/butai/", 1)

    assert len(result.listings) == 1


@pytest.mark.unit
def test_discovery_follows_pagination_that_carries_search_filters() -> None:
    html = """
    <a href='/butai/vilniuje/puslapis/2/'>2</a>
    <a href='/butai/vilniuje/senamiestis/puslapis/3/'>3</a>
    """

    result = discover_listing_links(html, "apartments", "https://www.aruodas.lt/butai/vilniuje/", 1)

    assert result.next_page_url == "https://www.aruodas.lt/butai/vilniuje/puslapis/2/"


@pytest.mark.unit
def test_discovery_ignores_pagination_for_the_other_property_type() -> None:
    html = "<a href='/namai/vilniuje/puslapis/2/'>2</a>"

    result = discover_listing_links(html, "apartments", "https://www.aruodas.lt/butai/vilniuje/", 1)

    assert result.next_page_url is None


@pytest.mark.unit
def test_discovery_tolerates_an_anchor_with_a_valueless_href() -> None:
    result = discover_listing_links("<a href>placeholder</a>", "apartments", "https://x/", 1)

    assert result.listings == ()


@pytest.mark.unit
def test_discovery_finds_rent_listings_and_the_next_rent_page() -> None:
    """A missed rent pattern yields no error, only an empty page-1 crawl."""
    html = Path("tests/fixtures/html/search_cards_apartments_rent.html").read_text(encoding="utf-8")

    result = discover_listing_links(
        html,
        property_type="apartments_rent",
        source_search_url="https://www.aruodas.lt/butu-nuoma/vilniuje/",
        source_page_number=1,
    )

    assert [item.listing_id for item in result.listings] == ["4-1495947", "4-1495948"]
    assert result.next_page_url == "https://www.aruodas.lt/butu-nuoma/vilniuje/puslapis/2/"


@pytest.mark.unit
def test_discovery_keeps_rent_and_sale_apartments_apart() -> None:
    html = Path("tests/fixtures/html/search_cards_apartments_rent.html").read_text(encoding="utf-8")

    sale = discover_listing_links(html, "apartments", "https://www.aruodas.lt/butai/vilniuje/", 1)
    houses = discover_listing_links(
        html, "houses_rent", "https://www.aruodas.lt/namu-nuoma/vilniuje/", 1
    )

    assert [item.listing_id for item in sale.listings] == ["1-1234567"]
    assert sale.next_page_url is None
    assert [item.listing_id for item in houses.listings] == ["5-73079"]
