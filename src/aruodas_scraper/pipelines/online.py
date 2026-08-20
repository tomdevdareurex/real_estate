"""Bounded online orchestration for Aruodas retrieval."""

import logging
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import chain
from pathlib import Path

from pydantic import ValidationError

from aruodas_scraper.cities import CategoryDefinition, CityRegistry, PropertyCategory
from aruodas_scraper.discovery.listing_links import discover_listing_links
from aruodas_scraper.discovery.pagination import PaginationState
from aruodas_scraper.exceptions import (
    BlockedError,
    DetailIdentityMismatch,
    ProxyAuthenticationError,
    RetrievalError,
)
from aruodas_scraper.models import (
    DiscoveryRecord,
    FailedUrl,
    ListingRecord,
    OnlineScrapeSummary,
    UnknownField,
)
from aruodas_scraper.networking.budget import (
    DEFAULT_MAX_EMPTY_BURSTS,
    BudgetPolicy,
    RequestBudget,
)
from aruodas_scraper.networking.http_client import AruodasHttpClient
from aruodas_scraper.parsers.apartment import parse_apartment
from aruodas_scraper.parsers.house import parse_house
from aruodas_scraper.parsers.search_card import parse_search_cards
from aruodas_scraper.pipelines.export import (
    read_records,
    write_failures,
    write_json,
    write_records,
    write_summary,
    write_unknown_fields,
)
from aruodas_scraper.validation.quality_report import build_quality_report

logger = logging.getLogger(__name__)

# The block is per source IP, carries a ~20-25 minute TTL, and every further request renews
# it instead of returning a page. So a refused URL is not unavailable, it was asked for at
# the wrong moment: one retry after the cooldown usually succeeds. A second retry does not,
# and only spends requests that the next burst would have used on unseen URLs.
_MAX_ATTEMPTS_PER_URL = 2

# Public because the CLI uses it as an option default. Slightly over the observed TTL:
# undershooting it means the retry lands inside the block and renews it.
DEFAULT_RETRY_COOLDOWN_SECONDS = 1500.0


def _backfill_from_card(detail: ListingRecord, card: ListingRecord) -> ListingRecord:
    """Carry over values the card stated and the detail page left unset.

    A detail record is richer overall but not a superset field by field: the card names the
    district, for one, where many detail pages do not. Replacing the record wholesale would
    drop data the run has already paid a request for.
    """
    missing = {
        name: getattr(card, name)
        for name in type(detail).model_fields
        if getattr(detail, name) is None and getattr(card, name) is not None
    }
    return detail.model_copy(update=missing) if missing else detail


def _merge_records(*groups: Iterable[ListingRecord]) -> list[ListingRecord]:
    """Collapse records by listing ID, later groups winning.

    A search-card record is a strict subset of a detail record, so it must never displace
    one even when it arrives later in the run. It is still worth reading, though: the card
    names fields no detail page states, so it backfills rather than being discarded.
    """
    merged: dict[str, ListingRecord] = {}
    for record in chain(*groups):
        current = merged.get(record.listing_id)
        if current is not None and current.record_source != record.record_source:
            if record.record_source == "search":
                merged[current.listing_id] = _backfill_from_card(current, record)
                continue
            record = _backfill_from_card(record, current)
        merged[record.listing_id] = record
    return list(merged.values())


@dataclass(frozen=True, slots=True)
class _DeferredListing:
    """A listing the origin refused, held over for one retry after a cooldown."""

    category: PropertyCategory
    listing: DiscoveryRecord
    referer: str


@dataclass(frozen=True, slots=True)
class _ListingOutcome:
    """Result of one attempt at a single listing."""

    record: ListingRecord | None = None
    unknown: tuple[UnknownField, ...] = ()
    failure: FailedUrl | None = None
    blocked: bool = False
    fetched: bool = False


def _selected_categories(property_type: str) -> tuple[PropertyCategory, ...]:
    if property_type == "all":
        return ("apartments", "houses")
    if property_type in {"apartments", "houses"}:
        return (property_type,)  # type: ignore[return-value]
    raise ValueError("property_type must be apartments, houses, or all")


def _queue_from_existing_cards(
    existing: dict[PropertyCategory, list[ListingRecord]],
    categories: tuple[PropertyCategory, ...],
    max_listings_per_category: int,
    shuffler: Callable[[list[DiscoveryRecord]], None],
) -> list[_DeferredListing]:
    """Queue detail fetches for listings a previous run only ever saw as a search card.

    Walking the search pages again to rediscover them would cost requests the origin will not
    sell twice: phase A and phase B draw on one per-IP budget, so every request spent on a page
    already harvested is one not spent giving a row its coordinates. The export already holds
    each listing's canonical URL, which is the only thing phase B actually needs.
    """
    queued: list[_DeferredListing] = []
    for category in categories:
        cards = [
            DiscoveryRecord(
                listing_id=record.listing_id,
                listing_url=record.listing_url,
                canonical_url=record.canonical_url,
                source_search_url=record.source_search_url,
                source_page_number=record.source_page_number,
                search_position=record.search_position,
            )
            for record in existing.get(category, [])
            if record.record_source == "search"
        ]
        # Same reasoning as the phase A visit order: reading every card in export order is a
        # pattern no human produces.
        shuffler(cards)
        queued.extend(
            _DeferredListing(category, card, card.source_search_url)
            for card in cards[:max_listings_per_category]
        )
    return queued


def _load_existing_records(path: Path) -> list[ListingRecord]:
    """Load a previous export so known listings can be skipped and preserved.

    Raises:
        ValueError: If the existing export cannot be read or parsed.
    """
    if not path.is_file():
        return []
    try:
        return read_records(path)
    except (OSError, UnicodeError, ValueError) as error:
        raise ValueError(
            f"Existing export could not be read: {path}. "
            "Use --overwrite to ignore it and rebuild from the live site."
        ) from error


def _parse_detail(
    category: PropertyCategory,
    html: str,
    source_search_url: str,
    source_page_number: int,
) -> tuple[ListingRecord, tuple[UnknownField, ...]]:
    if category == "apartments":
        return parse_apartment(html, source_search_url, source_page_number)
    return parse_house(html, source_search_url, source_page_number)


def _parse_search_page(
    search_html: str,
    category: PropertyCategory,
    definition: CategoryDefinition,
    page_number: int,
    city: str,
    current_url: str,
    failures: list[FailedUrl],
) -> tuple[ListingRecord, ...]:
    """Harvest card records, recording a parse failure instead of ending the run.

    A page that was served but whose markup changed still yielded pagination links, so the
    crawl should carry on and lose only this page's cards.
    """
    try:
        return parse_search_cards(
            search_html,
            category,
            definition.search_url,
            page_number,
            city=city,
        )
    except (ValidationError, ValueError) as error:
        failures.append(
            FailedUrl(
                url=current_url,
                stage="parse_search_cards",
                error_type=type(error).__name__,
                message=str(error),
            )
        )
        logger.warning("[%s] search page %s: no card records parsed.", category, current_url)
        return ()


def _attempt_listing(
    client: AruodasHttpClient,
    category: PropertyCategory,
    definition: CategoryDefinition,
    listing: DiscoveryRecord,
    referer: str,
    refresh_cache: bool,
) -> _ListingOutcome:
    """Retrieve, parse, and identity-check one listing without raising for its failure.

    Shared by the main traversal and the deferred retry pass so both treat a listing
    identically. Per-listing failures are returned as an outcome rather than raised,
    because one bad listing must not end the run.

    Raises:
        ProxyAuthenticationError: If an intercepting proxy demands credentials. That is a
            local configuration fault affecting every request, not a per-listing failure.
    """
    fetched = False
    try:
        detail_html = client.fetch(
            listing.canonical_url,
            refresh=refresh_cache,
            referer=referer,
        ).decode("utf-8")
        fetched = True
        record, unknown = _parse_detail(
            category,
            detail_html,
            listing.source_search_url,
            listing.source_page_number,
        )
        expected_property_type = "apartment" if category == "apartments" else "house"
        if (
            record.listing_id != listing.listing_id
            or not record.listing_id.startswith(definition.listing_id_prefix)
            or record.property_type != expected_property_type
        ):
            raise DetailIdentityMismatch(
                "Parsed detail identity does not match the discovered listing."
            )
    except ProxyAuthenticationError:
        raise
    except (DetailIdentityMismatch, RetrievalError, UnicodeError, ValueError) as error:
        return _ListingOutcome(
            failure=FailedUrl(
                url=listing.canonical_url,
                stage="retrieve_or_parse_detail",
                error_type=type(error).__name__,
                message=str(error),
            ),
            blocked=isinstance(error, BlockedError),
            fetched=fetched,
        )
    return _ListingOutcome(record=record, unknown=unknown, fetched=True)


def process_online(
    city: str,
    property_type: str,
    client: AruodasHttpClient,
    city_registry: CityRegistry,
    output_directory: Path,
    max_pages: int,
    max_listings_per_category: int,
    refresh_cache: bool = False,
    overwrite: bool = False,
    deepen: bool = True,
    retry_cooldown_seconds: float = DEFAULT_RETRY_COOLDOWN_SECONDS,
    max_cooldowns: int = 4,
    max_empty_bursts: int = DEFAULT_MAX_EMPTY_BURSTS,
    max_runtime_seconds: float | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    shuffler: Callable[[list[DiscoveryRecord]], None] = secrets.SystemRandom().shuffle,
) -> OnlineScrapeSummary:
    """Retrieve, parse, and export a bounded live snapshot.

    Listings already present in the export are skipped and carried forward unless
    ``overwrite`` is set. Records absent from this run are never removed. A category
    whose first search page is blocked is recorded as a failure and skipped, so one
    blocked category never discards another category's results.

    Requests are paced by a :class:`RequestBudget`. The origin serves a finite *number* of
    requests per source IP and then refuses everything for roughly 20-25 minutes, so the
    run stops its burst on the first refusal rather than firing the rest of its queue into
    the block, waits the block out, and resumes slightly below the burst size the origin
    last allowed. A refused URL is retried once on the far side of that cooldown, which is
    where blocked listings are recovered; anything still unfetched when the budget runs out
    is recorded as a failure rather than dropped.

    Args:
        retry_cooldown_seconds: Seconds to wait out a block before retrying. Zero disables
            cooldowns entirely, ending the run at the first block.
        max_cooldowns: How many blocks the run may wait out before giving up.
        max_empty_bursts: How many consecutive bursts may serve nothing before the run gives
            up. Guards against spending the whole cooldown allowance on a block that is not
            lapsing, which yields no pages at 25 minutes apiece.
        deepen: Spend the run on listings the export holds only as search cards, skipping
            phase A entirely. Falls back to a normal search walk when there are none, so a
            cold start still works. Turn it off to go discovering new listings instead.
        max_runtime_seconds: Wall-clock ceiling for the whole run, cooldowns included.
        sleeper: Injected for tests, so the cooldown does not really elapse.
        shuffler: Randomises the order detail pages are visited in. Injected for tests, which
            need a deterministic request sequence.

    Raises:
        RetrievalError: If the first search page is blocked for every selected category.
        ValueError: If a traversal bound or property type is invalid, or an
            existing export cannot be read.
    """
    if max_pages < 1:
        raise ValueError("max_pages must be at least one")
    if max_listings_per_category < 1:
        raise ValueError("max_listings_per_category must be at least one")
    if retry_cooldown_seconds < 0:
        raise ValueError("retry_cooldown_seconds cannot be negative")

    budget = RequestBudget(
        BudgetPolicy(
            cooldown_seconds=retry_cooldown_seconds,
            # A zero cooldown would retry straight back into the live block, so treat it as
            # the caller asking for no waiting at all.
            max_cooldowns=0 if retry_cooldown_seconds == 0 else max_cooldowns,
            max_empty_bursts=max_empty_bursts,
            max_runtime_seconds=max_runtime_seconds,
        ),
        sleeper=sleeper,
    )

    started = datetime.now(UTC)
    apartments: list[ListingRecord] = []
    houses: list[ListingRecord] = []
    unknown_fields: list[UnknownField] = []
    failures: list[FailedUrl] = []
    search_pages_fetched = 0
    listings_discovered = 0
    detail_pages_fetched = 0
    skipped_existing = 0
    blocked_categories = 0
    first_page_error: RetrievalError | None = None
    deferred_retries_attempted = 0
    deferred_retries_recovered = 0

    categories = _selected_categories(property_type)
    existing_records: dict[PropertyCategory, list[ListingRecord]] = {}
    for category in categories:
        definition = city_registry.get_category(city, category)
        try:
            existing_records[category] = _load_existing_records(
                output_directory / definition.output_filename
            )
        except ValueError:
            if not overwrite:
                raise
            existing_records[category] = []

    def flush() -> None:
        """Persist everything collected so far, overwriting the previous flush.

        A budgeted run spends most of its wall clock inside cooldowns, so it is measured in
        hours and is very likely to be interrupted. Writing only at the end would make a
        kill between bursts cost every burst already paid for, and those requests cannot be
        bought back for another 20-25 minutes each.
        """
        output_directory.mkdir(parents=True, exist_ok=True)
        for flushed in categories:
            flushed_definition = city_registry.get_category(city, flushed)
            collected = apartments if flushed == "apartments" else houses
            write_records(
                output_directory / flushed_definition.output_filename,
                _merge_records(existing_records.get(flushed, []), collected),
            )
        write_failures(output_directory / "failed_urls.csv", failures)

    # Phase A: harvest search-result pages. Each page carries ~25 cards that already hold
    # most of the schema, so a page costs one request but yields 25 records. Detail pages
    # are deliberately not fetched here: interleaving them spends the per-IP request budget
    # on page one's listings and the later search pages are never seen at all.
    #
    # It is skipped outright when the export already holds card-only listings and `deepen` is
    # set. Both phases draw on one per-IP budget, and phase A is greedy: 20 pages across two
    # categories is 40 requests, which is more than a whole run gets, so phase B was reached
    # with nothing left and no row ever gained coordinates. Deepening spends the entire budget
    # on detail pages instead. An export with no cards leaves the queue empty, and the walk
    # below runs as usual.
    queued: list[_DeferredListing] = (
        _queue_from_existing_cards(
            existing_records, categories, max_listings_per_category, shuffler
        )
        if deepen and not overwrite
        else []
    )
    if queued:
        logger.info(
            "Deepening %d listing(s) held only as search cards; skipping the search walk so "
            "the whole request budget goes to detail pages. Pass --no-deepen to discover "
            "new listings instead.",
            len(queued),
        )
    for category in () if queued else categories:
        if budget.stop_reason is not None:
            logger.warning("[%s] not attempted: %s.", category, budget.stop_reason)
            break
        definition = city_registry.get_category(city, category)
        # Only a detail record makes a listing done. A search-card record is a strict subset
        # of one, so treating it as done would leave every card-only listing permanently
        # shallow: the run after it would skip exactly the listings it still owes a fetch.
        detailed_ids = (
            frozenset()
            if overwrite
            else frozenset(
                record.listing_id
                for record in existing_records[category]
                if record.record_source == "detail"
            )
        )
        current_url: str | None = definition.search_url
        state = PaginationState()
        category_queued = 0
        page_number = 1
        page_attempts = 0
        search_referer: str | None = None

        while current_url is not None and page_number <= max_pages:
            if current_url in state.visited_urls:
                break
            if not budget.request_permitted():
                failures.append(
                    FailedUrl(
                        url=current_url,
                        stage="retrieve_search",
                        error_type="BudgetExhausted",
                        message=budget.stop_reason or "The request budget is spent.",
                    )
                )
                break
            try:
                search_html = client.fetch(
                    current_url, refresh=refresh_cache, referer=search_referer
                ).decode("utf-8")
            except ProxyAuthenticationError:
                raise
            except RetrievalError as error:
                if isinstance(error, BlockedError):
                    budget.record_block()
                    page_attempts += 1
                    if page_attempts < _MAX_ATTEMPTS_PER_URL:
                        # Same URL, so pagination is not renumbered and nothing is missed;
                        # the next permission check waits the block out first.
                        continue
                if page_number == 1:
                    blocked_categories += 1
                    first_page_error = first_page_error or error
                failures.append(
                    FailedUrl(
                        url=current_url,
                        stage="retrieve_search",
                        error_type="RetrievalError",
                        message=str(error),
                    )
                )
                logger.warning("[%s] FAILED search page %s: %s", category, current_url, error)
                break

            budget.record_success()
            search_pages_fetched += 1
            search_referer = current_url
            page_attempts = 0
            result = discover_listing_links(
                search_html,
                category,
                definition.search_url,
                page_number,
            )
            card_records = _parse_search_page(
                search_html, category, definition, page_number, city, current_url, failures
            )
            if category == "apartments":
                apartments.extend(card_records)
            else:
                houses.extend(card_records)
            logger.info(
                "[%s] search page %d yielded %d card record(s) from one request.",
                category,
                page_number,
                len(card_records),
            )

            new_listings = tuple(
                listing
                for listing in result.listings
                if listing.listing_id not in state.discovered_ids
            )
            # Pagination stop detection must see every new id, including skipped ones,
            # otherwise a page of entirely known listings looks like a dead end.
            new_ids = frozenset(listing.listing_id for listing in new_listings)
            unseen_listings = tuple(
                listing for listing in new_listings if listing.listing_id not in detailed_ids
            )
            skipped_existing += len(new_listings) - len(unseen_listings)
            remaining = max_listings_per_category - category_queued
            selected_listings = unseen_listings[:remaining]
            category_queued += len(selected_listings)
            listings_discovered += len(new_listings)
            # Which listings to keep is decided by page order, so the budget still goes to the
            # newest first; the order they are then *visited* in is not, because walking every
            # page's cards strictly top to bottom is a pattern no reader produces.
            visit_order = list(selected_listings)
            shuffler(visit_order)
            queued.extend(
                _DeferredListing(category, listing, current_url) for listing in visit_order
            )

            should_stop = state.should_stop(result.next_page_url, new_ids)
            state = PaginationState(
                visited_urls=state.visited_urls | {current_url},
                discovered_ids=state.discovered_ids | new_ids,
            )
            # Deliberately not stopping once the detail queue is full. `max_listings_per_category`
            # bounds phase B, which is one request per record; pagination is phase A, which is
            # one request per ~25. Ending the walk at the detail cap would throw away the whole
            # point of harvesting cards - a 60-listing cap would stop the crawl three pages in.
            # Card yield is bounded by `max_pages` alone.
            if should_stop:
                break
            current_url = result.next_page_url
            page_number += 1

    # Both operands are final once phase A ends, and a run that cannot reach any category's
    # first page has nothing to export, so it aborts before anything is written.
    if first_page_error is not None and blocked_categories == len(categories):
        raise first_page_error

    # Phase A is where the bulk of the yield comes from, so it is banked before phase B
    # spends the rest of the budget on requests that may well be refused.
    flush()

    # Phase B: spend what is left of the request budget on detail pages, which carry the
    # description, coordinates, engagement stats and fine-grained features that no card has.
    pending: deque[tuple[_DeferredListing, int]] = deque((item, 0) for item in queued)
    processed = 0
    while pending:
        item, attempts = pending.popleft()
        if budget.burst_is_spent and processed:
            # Bank the burst before the cooldown rather than after it. The wait is ~25 minutes,
            # so it is where a run is most likely to be killed, and rows held in memory across
            # it are rows the export loses.
            flush()
        if not budget.request_permitted():
            pending.appendleft((item, attempts))
            break
        if attempts:
            deferred_retries_attempted += 1
        definition = city_registry.get_category(city, item.category)
        outcome = _attempt_listing(
            client, item.category, definition, item.listing, item.referer, refresh_cache
        )
        processed += 1
        if outcome.fetched:
            detail_pages_fetched += 1
            budget.record_success()
        failure = outcome.failure
        if failure is None:
            if outcome.record is not None:
                if item.category == "apartments":
                    apartments.append(outcome.record)
                else:
                    houses.append(outcome.record)
            unknown_fields.extend(outcome.unknown)
            if attempts:
                deferred_retries_recovered += 1
            logger.info("[%s %d/%d] OK", item.category, processed, len(queued))
            continue

        if outcome.blocked:
            budget.record_block()
            if attempts + 1 < _MAX_ATTEMPTS_PER_URL:
                # Refused, not unavailable. Send it to the back of the queue so the burst
                # that resumes after the cooldown starts on listings never asked for yet.
                pending.append((item, attempts + 1))
                continue
        failures.append(failure)
        logger.warning(
            "[%s %d/%d] FAILED: %s", item.category, processed, len(queued), failure.message
        )

    for item, _ in pending:
        # Owed an attempt the budget could not fund. Recorded rather than dropped so the
        # export states what is missing instead of silently under-reporting.
        failures.append(
            FailedUrl(
                url=item.listing.canonical_url,
                stage="retrieve_or_parse_detail",
                error_type="BudgetExhausted",
                message=budget.stop_reason or "The request budget is spent.",
            )
        )

    final_apartments = _merge_records(existing_records.get("apartments", []), apartments)
    final_houses = _merge_records(existing_records.get("houses", []), houses)
    output_directory.mkdir(parents=True, exist_ok=True)
    for category in categories:
        definition = city_registry.get_category(city, category)
        records = final_apartments if category == "apartments" else final_houses
        write_records(output_directory / definition.output_filename, records)

    all_records = [*final_apartments, *final_houses]
    summary = OnlineScrapeSummary(
        city=city,
        started_at_utc=started,
        completed_at_utc=datetime.now(UTC),
        search_pages_fetched=search_pages_fetched,
        listings_discovered=listings_discovered,
        detail_pages_fetched=detail_pages_fetched,
        apartments_exported=len(final_apartments),
        houses_exported=len(final_houses),
        failed=len(failures),
        unknown_labels=len({item.label_lt for item in unknown_fields}),
        skipped_existing=skipped_existing,
        deferred_retries_attempted=deferred_retries_attempted,
        deferred_retries_recovered=deferred_retries_recovered,
    )
    write_summary(output_directory / "scrape_summary.json", summary)
    write_json(output_directory / "data_quality_report.json", build_quality_report(all_records))
    write_failures(output_directory / "failed_urls.csv", failures)
    write_unknown_fields(output_directory / "unknown_fields.csv", unknown_fields)
    return summary


__all__ = ["DEFAULT_RETRY_COOLDOWN_SECONDS", "process_online"]
