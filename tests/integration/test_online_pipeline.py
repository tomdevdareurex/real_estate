import csv
import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
import respx

from aruodas_scraper.cities import load_city_registry
from aruodas_scraper.constants import CSV_ENCODING
from aruodas_scraper.exceptions import ProxyAuthenticationError, RetrievalError
from aruodas_scraper.networking.cache import HtmlCache
from aruodas_scraper.networking.http_client import AruodasHttpClient, FetchOptions
from aruodas_scraper.networking.rate_limiter import DelayPolicy
from aruodas_scraper.pipelines.online import process_online

SEARCH_URL = "https://www.aruodas.lt/butai/vilniuje/"
DETAIL_URL = "https://www.aruodas.lt/butai-vilniuje-zirmunuose-testu-g-butas-1-1234567/"


@pytest.mark.integration
@respx.mock
def test_online_pipeline_discovers_parses_and_exports_bounded_listing(
    tmp_path: Path,
) -> None:
    search_html = Path("tests/fixtures/html/search_apartments.html").read_bytes()
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=search_html))
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    output_directory = tmp_path / "processed"

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path / "cache"),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=1, blocked_max_attempts=1),
        sleeper=Mock(),
    ) as client:
        summary = process_online(
            city="vilnius",
            property_type="apartments",
            client=client,
            city_registry=load_city_registry(Path("config/cities.yaml")),
            output_directory=output_directory,
            max_pages=1,
            max_listings_per_category=1,
        )

    assert summary.mode == "online"
    assert summary.search_pages_fetched == 1
    # Discovery is not bounded by the detail budget: the page carries two listings and both
    # are known about, while max_listings_per_category caps only the detail fetches.
    assert summary.listings_discovered == 2
    assert summary.detail_pages_fetched == 1
    assert summary.apartments_exported == 1
    assert summary.failed == 0
    assert {path.name for path in output_directory.iterdir()} == {
        "apartments_sale_vilnius.csv",
        "scrape_summary.json",
        "data_quality_report.json",
        "failed_urls.csv",
        "unknown_fields.csv",
        # Appended to rather than overwritten, so the growth of the dataset stays readable
        # after scrape_summary.json has been replaced by the next run.
        "run_history.csv",
    }

    with (output_directory / "apartments_sale_vilnius.csv").open(
        encoding="utf-8", newline=""
    ) as file_handle:
        rows = list(csv.DictReader(file_handle))
    assert [row["listing_id"] for row in rows] == ["1-1234567"]
    summary_data = json.loads((output_directory / "scrape_summary.json").read_text("utf-8"))
    assert summary_data["mode"] == "online"


@pytest.mark.integration
@respx.mock
def test_online_pipeline_fails_when_initial_search_cannot_be_retrieved(
    tmp_path: Path,
) -> None:
    route = respx.get(SEARCH_URL).mock(return_value=httpx.Response(404))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path / "cache"),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=1, blocked_max_attempts=1),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="HTTP 404"):
            process_online(
                city="vilnius",
                property_type="apartments",
                client=client,
                city_registry=load_city_registry(Path("config/cities.yaml")),
                output_directory=tmp_path / "processed",
                max_pages=1,
                max_listings_per_category=1,
            )

    assert route.call_count == 1
    assert not (tmp_path / "processed").exists()


@pytest.mark.integration
@respx.mock
def test_online_pipeline_rejects_mismatched_detail_identity(tmp_path: Path) -> None:
    search_html = Path("tests/fixtures/html/search_apartments.html").read_bytes()
    house_html = Path("tests/fixtures/html/house_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=search_html))
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=house_html))
    output_directory = tmp_path / "processed"

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path / "cache"),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=1, blocked_max_attempts=1),
        sleeper=Mock(),
    ) as client:
        summary = process_online(
            city="vilnius",
            property_type="apartments",
            client=client,
            city_registry=load_city_registry(Path("config/cities.yaml")),
            output_directory=output_directory,
            max_pages=1,
            max_listings_per_category=1,
        )

    assert summary.apartments_exported == 0
    assert summary.failed == 1
    assert "DetailIdentityMismatch" in (output_directory / "failed_urls.csv").read_text("utf-8")


SECOND_DETAIL_URL = "https://www.aruodas.lt/butai-vilniuje-naujamiestyje-kodo-g-butas-1-7654321/"
SECOND_PAGE_URL = "https://www.aruodas.lt/butai/puslapis/2/"
HOUSES_SEARCH_URL = "https://www.aruodas.lt/namai/vilniuje/"
EMPTY_SEARCH_HTML = (
    b'<!doctype html><html lang="lt"><body><main id="search-results"></main></body></html>'
)


def _listing_ids(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as file_handle:
        return [row["listing_id"] for row in csv.DictReader(file_handle)]


def _run(tmp_path: Path, cache_name: str, **overrides: object) -> object:
    # Cooldowns are off unless a test asks for them, so tests covering the main traversal
    # keep asserting exactly the requests that traversal makes and nothing waits.
    arguments: dict[str, object] = {
        "property_type": "apartments",
        "max_pages": 1,
        "max_listings_per_category": 1,
        "retry_cooldown_seconds": 0.0,
        "sleeper": Mock(),
        # Live runs randomise the order detail pages are visited in; these tests assert an
        # exact request sequence, so the shuffle is a no-op here.
        "shuffler": lambda _listings: None,
    }
    arguments.update(overrides)
    with AruodasHttpClient(
        cache=HtmlCache(tmp_path / cache_name),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=1, blocked_max_attempts=1),
        sleeper=Mock(),
    ) as client:
        return process_online(
            city="vilnius",
            client=client,
            city_registry=load_city_registry(Path("config/cities.yaml")),
            output_directory=tmp_path / "processed",
            **arguments,  # type: ignore[arg-type]
        )


def _rows(path: Path) -> list[dict[str, str]]:
    with path.open(encoding=CSV_ENCODING, newline="") as file_handle:
        return list(csv.DictReader(file_handle))


@pytest.mark.integration
@respx.mock
def test_search_cards_are_exported_for_listings_the_detail_budget_never_reaches(
    tmp_path: Path,
) -> None:
    # The point of phase A: one search request yields a record for every card on the page,
    # whether or not the per-IP request budget stretches to that listing's detail page.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    first_detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    second_detail = respx.get(SECOND_DETAIL_URL).mock(return_value=httpx.Response(403))

    summary = _run(tmp_path, "cache-one", max_listings_per_category=1)

    assert search.call_count == 1
    assert first_detail.call_count == 1
    assert second_detail.call_count == 0

    exported = _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    rows = {row["listing_id"]: row for row in exported}
    assert set(rows) == {"1-1234567", "1-7654321"}
    # The listing that got a detail fetch keeps the richer record; the other still lands in
    # the export from its card alone.
    assert rows["1-1234567"]["record_source"] == "detail"
    assert rows["1-7654321"]["record_source"] == "search"
    assert rows["1-7654321"]["price_eur"] == "75000.0"
    assert rows["1-7654321"]["rooms"] == "1"
    assert summary.apartments_exported == 2  # type: ignore[attr-defined]
    assert summary.failed == 0  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_a_detail_record_inherits_card_fields_its_own_page_never_stated(tmp_path: Path) -> None:
    # A detail record outranks a card but is not a superset of it field by field: live detail
    # pages routinely omit the district that the card names. Replacing the card wholesale threw
    # away data the run had already spent a request on.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    without_district = detail_html.replace(
        "Vilnius, Žirmūnai, Testų g.".encode(), "Vilnius, Testų g.".encode()
    )
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=without_district))

    _run(tmp_path, "cache-one", max_listings_per_category=1)

    row = {
        r["listing_id"]: r for r in _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    }["1-1234567"]
    assert row["record_source"] == "detail"
    assert row["district"] == "Žirmūnai"
    # Backfilling must not let a stale card overwrite what the detail page did state.
    assert row["street"] == "Testų g."


@pytest.mark.integration
@respx.mock
def test_a_later_run_deepens_a_listing_that_only_has_a_search_record(tmp_path: Path) -> None:
    # A search card is a strict subset of a detail record, so a listing holding only a card
    # is not done. Treating it as done would make the first run's budget shortfall permanent.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    second_detail = respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    export = tmp_path / "processed" / "apartments_sale_vilnius.csv"

    _run(tmp_path, "cache-one", max_listings_per_category=1)
    first_pass = {row["listing_id"]: row for row in _rows(export)}
    assert first_pass["1-7654321"]["record_source"] == "search"
    assert second_detail.call_count == 0

    _run(tmp_path, "cache-two", max_listings_per_category=2)

    # The already-detailed listing is left alone; the card-only one is finally fetched.
    assert second_detail.call_count == 1
    second_pass = {row["listing_id"]: row for row in _rows(export)}
    assert second_pass["1-1234567"]["record_source"] == "detail"
    assert second_pass["1-7654321"]["record_source"] == "detail"


@pytest.mark.integration
@respx.mock
def test_deepening_spends_the_whole_budget_on_details_and_never_rewalks_search(
    tmp_path: Path,
) -> None:
    # Both phases draw on one per-IP request budget and the search walk is greedy: max_pages
    # across every category is more than a whole run gets, so phase B was reached with nothing
    # left and rows kept arriving without coordinates. A deepening run buys no page twice.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    second_detail = respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    _run(tmp_path, "cache-one", max_listings_per_category=1)
    searches_spent_discovering = search.call_count

    summary = _run(tmp_path, "cache-two", max_listings_per_category=1, deepen=True)

    assert search.call_count == searches_spent_discovering
    assert summary.search_pages_fetched == 0  # type: ignore[attr-defined]
    assert second_detail.call_count == 1
    # The point of the whole exercise: the card-only row gains the coordinates only a detail
    # page carries.
    deepened = {
        r["listing_id"]: r for r in _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    }
    assert deepened["1-7654321"]["record_source"] == "detail"
    assert deepened["1-7654321"]["latitude"] == "54.712345"


@pytest.mark.integration
@respx.mock
def test_deepening_still_walks_search_when_no_listing_is_card_only(tmp_path: Path) -> None:
    # Deepening must not mean "never discover". A fresh checkout has no export to deepen, and
    # an export whose every row is already a detail record has nothing left to deepen either;
    # both must fall through to the search walk rather than making a no-op run.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    cold_start = _run(tmp_path, "cache-one", max_listings_per_category=2, deepen=True)
    assert search.call_count == 1
    assert cold_start.search_pages_fetched == 1  # type: ignore[attr-defined]
    assert cold_start.detail_pages_fetched == 2  # type: ignore[attr-defined]

    fully_detailed = _run(tmp_path, "cache-two", max_listings_per_category=2, deepen=True)

    assert search.call_count == 2
    assert fully_detailed.search_pages_fetched == 1  # type: ignore[attr-defined]
    assert fully_detailed.skipped_existing == 2  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_a_re_seen_card_backfills_a_detail_row_it_arrives_after(tmp_path: Path) -> None:
    # The mirror of the within-run backfill above. A card meeting an existing detail row was
    # discarded wholesale, silently dropping the district, image URLs and search position that
    # no detail page states - so a discovery run could strip fields off rows it never fetched.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    search = respx.get(SEARCH_URL).mock(
        side_effect=[
            # A page whose listing links carry no cards, so the first run's detail row is
            # written with only what the detail page itself states.
            httpx.Response(
                200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
            ),
            httpx.Response(
                200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
            ),
        ]
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )
    export = tmp_path / "processed" / "apartments_sale_vilnius.csv"

    _run(tmp_path, "cache-one", max_listings_per_category=1)
    assert {r["listing_id"]: r for r in _rows(export)}["1-1234567"]["district"] == ""

    _run(tmp_path, "cache-two", max_listings_per_category=2, deepen=False)

    assert search.call_count == 2
    row = {r["listing_id"]: r for r in _rows(export)}["1-1234567"]
    assert row["district"] == "Žirmūnai"
    # The card fills gaps only. It must not demote the row or overwrite what the detail page
    # already stated.
    assert row["record_source"] == "detail"
    assert row["latitude"] == "54.712345"
    assert row["street"] == "Testų g."


@pytest.mark.integration
@respx.mock
def test_phase_a_records_survive_a_run_that_dies_during_detail_retrieval(tmp_path: Path) -> None:
    # A budgeted run is measured in hours of cooldowns, so it is likely to be interrupted.
    # Search-card yield is banked before phase B starts spending on requests that may fail.
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(407))

    with pytest.raises(ProxyAuthenticationError):
        _run(tmp_path, "cache-one", max_listings_per_category=2)

    # The run never reached its own final export, yet phase A's cards are already on disk.
    rows = {
        row["listing_id"]: row
        for row in _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    }
    assert set(rows) == {"1-1234567", "1-7654321"}
    assert rows["1-7654321"]["price_eur"] == "75000.0"


@pytest.mark.integration
@respx.mock
def test_detail_rows_survive_a_run_that_dies_during_the_cooldown(tmp_path: Path) -> None:
    # A cooldown is ~25 minutes of doing nothing, so it is where a run is most likely to be
    # killed. Detail pages cost one request each and are the only source of coordinates, so a
    # burst held in memory across that wait is the most expensive thing a run can lose.
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
        )
    )
    # Refused, which spends the burst and sends the run into the cooldown it dies in.
    respx.get(SECOND_DETAIL_URL).mock(return_value=httpx.Response(403))
    sleeper = Mock(side_effect=RuntimeError("killed while waiting out the block"))

    with pytest.raises(RuntimeError):
        _run(
            tmp_path,
            "cache-one",
            max_listings_per_category=2,
            retry_cooldown_seconds=120.0,
            sleeper=sleeper,
        )

    sleeper.assert_called_once_with(120.0)
    rows = {
        row["listing_id"]: row
        for row in _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    }
    assert rows["1-1234567"]["record_source"] == "detail"
    assert rows["1-1234567"]["latitude"] == "54.712345"


@pytest.mark.integration
@respx.mock
def test_online_pipeline_skips_listings_already_present_in_the_export(tmp_path: Path) -> None:
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    first_detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    second_detail = respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    _run(tmp_path, "cache-one")
    summary = _run(tmp_path, "cache-two")

    assert first_detail.call_count == 1
    assert second_detail.call_count == 1
    assert summary.skipped_existing == 1  # type: ignore[attr-defined]
    assert summary.detail_pages_fetched == 1  # type: ignore[attr-defined]
    assert _listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv") == [
        "1-1234567",
        "1-7654321",
    ]


@pytest.mark.integration
@respx.mock
def test_online_pipeline_refetches_known_listings_when_overwrite_is_requested(
    tmp_path: Path,
) -> None:
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    first_detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))

    _run(tmp_path, "cache-one")
    summary = _run(tmp_path, "cache-two", overwrite=True)

    assert first_detail.call_count == 2
    assert summary.skipped_existing == 0  # type: ignore[attr-defined]
    assert _listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv") == ["1-1234567"]


@pytest.mark.integration
@respx.mock
def test_online_pipeline_continues_paginating_when_a_page_is_entirely_known(
    tmp_path: Path,
) -> None:
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )
    second_page = respx.get(SECOND_PAGE_URL).mock(
        return_value=httpx.Response(200, content=EMPTY_SEARCH_HTML)
    )

    _run(tmp_path, "cache-one", max_pages=2, max_listings_per_category=2)
    summary = _run(tmp_path, "cache-two", max_pages=2, max_listings_per_category=2)

    # Both runs walk to max_pages: pagination is bounded by max_pages alone, because search
    # pages are the cheap half of the run. The second run reaching page 2 is the point here -
    # a page whose listings are all already detailed must not read as the end of the results.
    assert second_page.call_count == 2
    assert summary.skipped_existing == 2  # type: ignore[attr-defined]
    assert summary.detail_pages_fetched == 0  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_the_detail_cap_does_not_cut_the_search_walk_short(tmp_path: Path) -> None:
    # The detail cap bounds phase B, which costs one request per record. Letting it also stop
    # pagination would throw away the reason cards are harvested at all: one search request
    # carries ~25 records, so a cap of 1 would end the crawl on page 1 and lose the rest.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    second_page = respx.get(SECOND_PAGE_URL).mock(
        return_value=httpx.Response(200, content=EMPTY_SEARCH_HTML)
    )

    summary = _run(tmp_path, "cache", max_pages=2, max_listings_per_category=1)

    assert second_page.call_count == 1
    assert summary.search_pages_fetched == 2  # type: ignore[attr-defined]
    assert summary.detail_pages_fetched == 1  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_online_pipeline_preserves_unseen_rows_when_overwriting(tmp_path: Path) -> None:
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(return_value=httpx.Response(200, content=detail_html))
    respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    _run(tmp_path, "cache-one", max_listings_per_category=2)
    _run(tmp_path, "cache-two", max_listings_per_category=1, overwrite=True)

    assert _listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv") == [
        "1-1234567",
        "1-7654321",
    ]


@pytest.mark.integration
@respx.mock
def test_online_pipeline_keeps_one_category_when_another_first_page_is_blocked(
    tmp_path: Path,
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
        )
    )
    houses_search = respx.get(HOUSES_SEARCH_URL).mock(return_value=httpx.Response(403))

    summary = _run(tmp_path, "cache-one", property_type="all", retry_cooldown_seconds=120.0)

    # Retried once on the far side of the cooldown, because a 403 means "not now". Only then
    # is the page given up on, and the apartments already queued are still retrieved.
    assert houses_search.call_count == 2
    assert summary.apartments_exported == 1  # type: ignore[attr-defined]
    assert summary.failed == 1  # type: ignore[attr-defined]
    assert _listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv") == ["1-1234567"]
    failures = (tmp_path / "processed" / "failed_urls.csv").read_text("utf-8")
    assert "retrieve_search" in failures
    assert HOUSES_SEARCH_URL in failures


@pytest.mark.integration
@respx.mock
def test_online_pipeline_stops_requesting_once_the_origin_blocks_repeatedly(
    tmp_path: Path,
) -> None:
    # A block is per source IP and self-clearing, so every detail request after it renews
    # the block instead of retrieving anything. The burst must end on the first refusal.
    # With no cooldown allowed there is nothing left to wait for, so the run ends there.
    # Search pages are fetched in phase A, before any detail page, so they are already done.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    blocked_detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(403))
    later_detail = respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )
    houses_search = respx.get(HOUSES_SEARCH_URL).mock(
        return_value=httpx.Response(200, content=EMPTY_SEARCH_HTML)
    )

    summary = _run(tmp_path, "cache-one", property_type="all", max_listings_per_category=2)

    assert blocked_detail.call_count == 1
    assert later_detail.call_count == 0
    assert houses_search.call_count == 1
    # Both the refused listing and the one never asked for are reported: a listing the run
    # could not fund an attempt for is missing from the export either way.
    assert summary.failed == 2  # type: ignore[attr-defined]
    assert summary.apartments_exported == 0  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_online_pipeline_leaves_the_remaining_categories_alone_when_it_cannot_wait(
    tmp_path: Path,
) -> None:
    # The block is per source IP, so it applies to every category at once. With no cooldown
    # allowed there is nothing that would clear it, and asking houses for a page would only
    # renew the block. The run stops and says why rather than walking into it.
    apartments_search = respx.get(SEARCH_URL).mock(return_value=httpx.Response(403))
    houses_search = respx.get(HOUSES_SEARCH_URL).mock(return_value=httpx.Response(200))

    summary = _run(tmp_path, "cache-one", property_type="all", retry_cooldown_seconds=0.0)

    assert apartments_search.call_count == 1
    assert houses_search.call_count == 0
    assert summary.failed == 1  # type: ignore[attr-defined]
    failures = (tmp_path / "processed" / "failed_urls.csv").read_text("utf-8")
    assert "BudgetExhausted" in failures


@pytest.mark.integration
@respx.mock
def test_online_pipeline_recovers_a_blocked_listing_after_the_cooldown(tmp_path: Path) -> None:
    # A 403 means "not now", not "not available": the same URL served on a later attempt
    # must end up in the export and must not be reported as a failure of the run.
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    detail = respx.get(DETAIL_URL).mock(
        side_effect=[
            httpx.Response(403),
            httpx.Response(
                200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
            ),
        ]
    )
    sleeper = Mock()

    summary = _run(tmp_path, "cache-one", retry_cooldown_seconds=120.0, sleeper=sleeper)

    assert detail.call_count == 2
    assert summary.deferred_retries_attempted == 1  # type: ignore[attr-defined]
    assert summary.deferred_retries_recovered == 1  # type: ignore[attr-defined]
    assert summary.failed == 0  # type: ignore[attr-defined]
    assert summary.apartments_exported == 1  # type: ignore[attr-defined]
    assert _listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv") == ["1-1234567"]
    assert DETAIL_URL not in (tmp_path / "processed" / "failed_urls.csv").read_text("utf-8")
    sleeper.assert_called_once_with(120.0)


@pytest.mark.integration
@respx.mock
def test_online_pipeline_reports_a_listing_still_blocked_after_the_cooldown(
    tmp_path: Path,
) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(403))

    summary = _run(tmp_path, "cache-one", retry_cooldown_seconds=120.0, sleeper=Mock())

    assert detail.call_count == 2
    assert summary.deferred_retries_attempted == 1  # type: ignore[attr-defined]
    assert summary.deferred_retries_recovered == 0  # type: ignore[attr-defined]
    assert summary.failed == 1  # type: ignore[attr-defined]
    failures = (tmp_path / "processed" / "failed_urls.csv").read_text("utf-8")
    assert DETAIL_URL in failures
    assert "BlockedError" in failures


@pytest.mark.integration
@respx.mock
def test_online_pipeline_skips_the_retry_pass_when_the_cooldown_is_zero(tmp_path: Path) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    detail = respx.get(DETAIL_URL).mock(return_value=httpx.Response(403))
    sleeper = Mock()

    summary = _run(tmp_path, "cache-one", retry_cooldown_seconds=0.0, sleeper=sleeper)

    assert detail.call_count == 1
    assert summary.deferred_retries_attempted == 0  # type: ignore[attr-defined]
    assert summary.failed == 1  # type: ignore[attr-defined]
    sleeper.assert_not_called()


@pytest.mark.integration
@respx.mock
def test_online_pipeline_retries_listings_it_never_reached_before_abandoning(
    tmp_path: Path,
) -> None:
    # A block ends the burst before the queue is drained, so the listings behind it were
    # never requested. The cooldown is exactly what clears that block, so once it lapses
    # both the refused listing and the ones behind it are still owed an attempt.
    detail_html = Path("tests/fixtures/html/apartment_detail.html").read_bytes()
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    blocked = respx.get(DETAIL_URL).mock(
        side_effect=[httpx.Response(403), httpx.Response(200, content=detail_html)]
    )
    never_reached = respx.get(SECOND_DETAIL_URL).mock(
        return_value=httpx.Response(200, content=detail_html.replace(b"1-1234567", b"1-7654321"))
    )

    summary = _run(
        tmp_path,
        "cache-one",
        max_listings_per_category=2,
        retry_cooldown_seconds=120.0,
        sleeper=Mock(),
    )

    # The first listing was refused and went to the back of the queue, so the cooldown is
    # spent on the second listing first and the refused one is retried after it. Only that
    # one listing counts as a retry: the second was never blocked, merely queued behind it.
    assert blocked.call_count == 2
    assert never_reached.call_count == 1
    assert summary.deferred_retries_attempted == 1  # type: ignore[attr-defined]
    assert summary.deferred_retries_recovered == 1  # type: ignore[attr-defined]
    assert summary.failed == 0  # type: ignore[attr-defined]
    assert sorted(_listing_ids(tmp_path / "processed" / "apartments_sale_vilnius.csv")) == [
        "1-1234567",
        "1-7654321",
    ]


CARDS_PER_PAGE = 25
PAGES_WALKED = 10


def _synthetic_card(listing_id: str, url: str) -> str:
    return f"""
      <div class="list-row-v2 object-row" data-uid="{listing_id}">
        <div class="list-adress-v2"><h3><a href="{url}">
          Zirmunai<br><strong>Testu g.</strong>
        </a></h3></div>
        <div class="list-params-block-v2">
          <div class="list-RoomNum-v2 list-detail-v2">
            <span class="list-detail-value-v2">3 k.</span>
          </div>
          <div class="list-AreaOverall-v2 list-detail-v2">
            <span class="list-detail-value-v2">65.3 m2</span>
          </div>
        </div>
        <div class="price"><span class="list-item-price-v2">123 400 &euro;</span></div>
      </div>
    """


def _synthetic_search_page(page_number: int) -> bytes:
    """Build a results page of `CARDS_PER_PAGE` cards that links to the next one.

    The next-page href carries a filter segment (`/butai/vilniuje/puslapis/N/`) because that
    is the shape Aruodas actually serves; matching only the unfiltered `/butai/puslapis/N/`
    silently capped every live crawl at page one.
    """
    cards = [
        _synthetic_card(
            "1-1234567" if page_number == 1 and index == 0 else f"1-{page_number}{index:02d}000",
            (
                DETAIL_URL
                if page_number == 1 and index == 0
                else f"/butai-vilniuje-test-1-{page_number}{index:02d}000/"
            ),
        )
        for index in range(CARDS_PER_PAGE)
    ]
    following = f'<a href="/butai/vilniuje/puslapis/{page_number + 1}/">next</a>'
    body = "".join(cards) + following
    return (
        '<!doctype html><html lang="lt"><body>'
        f'<main id="search-results">{body}</main></body></html>'
    ).encode()


@pytest.mark.integration
@respx.mock
def test_the_search_walk_multiplies_records_far_beyond_one_record_per_request(
    tmp_path: Path,
) -> None:
    # The whole strategy rests on this ratio. The ceiling is a per-IP request *count*, so the
    # only lever that raises yield is collecting more records per request: ten search requests
    # plus one detail request must return 250 records, not eleven.
    respx.get(SEARCH_URL).mock(return_value=httpx.Response(200, content=_synthetic_search_page(1)))
    for page_number in range(2, PAGES_WALKED + 1):
        respx.get(f"https://www.aruodas.lt/butai/vilniuje/puslapis/{page_number}/").mock(
            return_value=httpx.Response(200, content=_synthetic_search_page(page_number))
        )
    detail = respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
        )
    )

    summary = _run(tmp_path, "cache-one", max_pages=PAGES_WALKED, max_listings_per_category=1)

    assert summary.search_pages_fetched == PAGES_WALKED  # type: ignore[attr-defined]
    assert detail.call_count == 1
    assert summary.failed == 0  # type: ignore[attr-defined]

    rows = _rows(tmp_path / "processed" / "apartments_sale_vilnius.csv")
    assert len(rows) == CARDS_PER_PAGE * PAGES_WALKED
    assert sum(row["record_source"] == "search" for row in rows) == 249
    assert sum(row["record_source"] == "detail" for row in rows) == 1


@pytest.mark.integration
@respx.mock
def test_online_pipeline_refuses_to_run_against_an_unreadable_export(tmp_path: Path) -> None:
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_apartments.html").read_bytes()
        )
    )
    export = tmp_path / "processed" / "apartments_sale_vilnius.csv"
    export.parent.mkdir(parents=True)
    export.write_text("listing_id\n1-1234567\n", encoding="utf-8")

    with pytest.raises(ValueError, match="--overwrite"):
        _run(tmp_path, "cache-one")


@pytest.mark.integration
@respx.mock
def test_a_repeat_walk_over_the_same_pages_reports_no_new_publications(tmp_path: Path) -> None:
    # The question a scheduled re-run has to answer is "what appeared since last time", and
    # `listings_discovered` cannot answer it: a second walk re-sees every card it saw before,
    # so that count stays high while the dataset stops growing. `listings_new` is the one
    # that falls to zero, and the run history is where that series is kept.
    respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
        )
    )
    # The second walk finds the first listing already detailed and reaches for the other one.
    # Refusing it keeps this test about counting rather than about parsing a second fixture.
    respx.get(SECOND_DETAIL_URL).mock(return_value=httpx.Response(403))

    first = _run(tmp_path, "cache-one", deepen=False, max_listings_per_category=1)
    second = _run(tmp_path, "cache-two", deepen=False, max_listings_per_category=1)

    assert first.listings_new == 2  # type: ignore[attr-defined]
    assert second.listings_new == 0  # type: ignore[attr-defined]
    # Effort was identical both times; only the growth differs.
    assert second.listings_discovered == first.listings_discovered  # type: ignore[attr-defined]

    history = _rows(tmp_path / "processed" / "run_history.csv")
    assert [row["listings_new"] for row in history] == ["2", "0"]
    assert [row["total_known"] for row in history] == ["2", "2"]


@pytest.mark.integration
@respx.mock
def test_replaying_cached_pages_costs_no_request_and_is_not_counted_as_one(
    tmp_path: Path,
) -> None:
    # A run that dies mid-walk keeps no record of the page it reached, so the next one starts
    # at page 1 and replays everything already on disk. Those pages never reach the origin, so
    # charging them against the per-IP budget would spend a whole burst before the walk got
    # back to new ground - and reporting them as "fetched" would overstate what the run cost.
    # Both runs share one cache directory, which is what makes the second a replay.
    search = respx.get(SEARCH_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/search_cards_apartments.html").read_bytes()
        )
    )
    detail = respx.get(DETAIL_URL).mock(
        return_value=httpx.Response(
            200, content=Path("tests/fixtures/html/apartment_detail.html").read_bytes()
        )
    )
    respx.get(SECOND_DETAIL_URL).mock(return_value=httpx.Response(403))

    first = _run(tmp_path, "shared-cache", deepen=False, max_listings_per_category=1)
    second = _run(tmp_path, "shared-cache", deepen=False, max_listings_per_category=1)

    # The origin saw the search page once, no matter that it was walked twice.
    assert search.call_count == 1
    assert detail.call_count == 1

    assert first.search_pages_fetched == 1  # type: ignore[attr-defined]
    assert first.pages_served_from_cache == 0  # type: ignore[attr-defined]

    # The replay is free, and says so.
    assert second.search_pages_fetched == 0  # type: ignore[attr-defined]
    assert second.pages_served_from_cache >= 1  # type: ignore[attr-defined]
    # The cards are still parsed out of the cached page, so the walk really did continue.
    assert second.listings_discovered == first.listings_discovered  # type: ignore[attr-defined]


@pytest.mark.integration
@respx.mock
def test_a_rent_run_exports_rent_rows_to_the_rent_file(tmp_path: Path) -> None:
    """Every mistake in the rent path exports an empty file and exits clean, so assert on rows."""
    rent_search_url = "https://www.aruodas.lt/butu-nuoma/vilniuje/"
    rent_detail_url = (
        "https://www.aruodas.lt/"
        "butu-nuoma-vilniuje-zirmunuose-verkiu-g-isnuomojamas-jaukus-kambariu-butas-su-4-1495947/"
    )
    search_html = Path("tests/fixtures/html/search_cards_apartments_rent.html").read_bytes()
    detail_html = (
        Path("tests/fixtures/html/apartment_detail.html")
        .read_text(encoding="utf-8")
        .replace(
            "butai-vilniuje-zirmunuose-testu-g-butas-1-1234567",
            "butu-nuoma-vilniuje-zirmunuose-verkiu-g-isnuomojamas-jaukus-kambariu-butas-su-4-1495947",
        )
        .encode("utf-8")
    )
    respx.get(rent_search_url).mock(return_value=httpx.Response(200, content=search_html))
    respx.get(rent_detail_url).mock(return_value=httpx.Response(200, content=detail_html))
    output_directory = tmp_path / "processed"

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path / "cache"),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=1, blocked_max_attempts=1),
        sleeper=Mock(),
    ) as client:
        summary = process_online(
            city="vilnius",
            property_type="apartments",
            client=client,
            city_registry=load_city_registry(Path("config/cities.yaml")),
            output_directory=output_directory,
            max_pages=1,
            max_listings_per_category=1,
            deal_type="rent",
        )

    assert summary.failed == 0
    assert not (output_directory / "apartments_sale_vilnius.csv").exists()

    rows = _rows(output_directory / "apartments_rent_vilnius.csv")
    assert [row["listing_id"] for row in rows] == ["4-1495947", "4-1495948"]
    assert {row["listing_type"] for row in rows} == {"rent"}
    assert {row["property_type"] for row in rows} == {"apartment"}
    assert rows[0]["record_source"] == "detail"
