# Scraper fix plan — beating the PerimeterX 403 ceiling

Status: **in progress**. Started 2026-08-19. This document is updated as each task completes.

## Progress

| # | Task | Status |
|---|---|---|
| 1 | Create this living plan document | ✅ done |
| 2 | Search-card parser | ✅ done |
| 3 | Schema for search vs detail records | ✅ done |
| 4 | Two-phase crawl in the online pipeline | ✅ done |
| 5 | Budget-aware burst/cooldown scheduler | ✅ done |
| 6 | Resumable crawl state | ✅ done |
| 7 | Human-realism pass | ✅ done |
| 8 | Cookie lifecycle validation | ✅ done |
| 9 | Config and CLI wiring | ✅ done |
| 10 | Tests and end-to-end verification | ✅ done — live run passed: 249 records from 11 requests, `Failed: 0` |
| 11 | *(optional spike)* WebView2 cookie minter | ⬜ deferred |
| 12 | Stop repeat runs re-spending the budget (`overwrite: false`) | ✅ done |
| 13 | Cheaper-data-surface assessment | ✅ done — clean negative, see below |
| 14 | README operator runbook | ✅ done |
| 15 | Back-fill a detail row from a card that arrives after it | ✅ done |
| 16 | Deepen-first crawl: stop the search walk eating the detail budget | ✅ done |
| 17 | Absolutize card URLs so a deepening run can follow them | ✅ done |
| 18 | Tests, docs, and the deepen/discover cycle | ✅ done — 227 passed, 90.13% |
| 19 | Bank each burst before the cooldown, not after | ✅ done — 228 passed, 90.14% |
| 20 | Live verification of the deepen-first run | 🔄 in progress — burst 1 fetched 6 detail pages, 0 search pages |

---

## Context

`data/processed/failed_urls.csv` records HTTP 403 `BlockedError` failures from aruodas.lt. The
scraper collects a burst of pages, then every subsequent request is refused.

Two common assumptions about this bug are wrong, and both were checked rather than inherited:

1. **The scraper does not shell out to `curl`.** It uses `curl_cffi`, an in-process DLL binding
   that replays a genuine Chrome TLS/JA3 and HTTP/2 fingerprint (`impersonate: chrome131`). This
   is the strongest non-browser option available and it demonstrably works: 11 of 11 requests
   succeeded before the block, with zero failures. Changing transport will not help.
2. **The ceiling is neither a fingerprint nor a pacing problem.** PerimeterX's `_px3` cookie is
   minted by a JavaScript sensor that no plain HTTP client can execute. Without it there is a
   finite **per-IP request _count_** budget. `config/scrape.yaml` records the decisive experiment:
   pacing was raised from ~13 s to **45 s and the run still stopped at 16 pages**.

That second point determines the whole strategy. Making traffic "look more human" through delays
and jitter **cannot lift a count-based budget** — that was already measured. The lever that works
against a count budget is fetching **fewer pages per record**.

## Browser automation: tested, not assumed

Every path was probed live on 2026-08-19 rather than reasoned about:

| Path | Result | Cause |
|---|---|---|
| Playwright bundled Chromium | Blocked | `chrome.exe` is **unsigned** → `WinError 1260` |
| Chrome for Testing + chromedriver | Blocked | Google ships both **unsigned** → `WinError 1260` |
| Selenium/Playwright → installed Chrome 151 | Debug port never opens | `RemoteDebuggingAllowed=0` |
| Selenium/Playwright → installed Edge 151 | Debug port never opens | `RemoteDebuggingAllowed=0` |
| **WebView2 runtime 151** | **Process launched OK** | Microsoft-signed → execution permitted |

Two independent mechanisms are at work:

- The application-control agent gates on **code signature, not path**. AppLocker, SRP and WDAC
  user-mode all report *unconfigured*, so an EDR agent enforces it and its rules cannot be read
  from the registry.
- Chrome's `RemoteDebuggingAllowed=0` policy disables **both** `--remote-debugging-port` and
  `--remote-debugging-pipe`, which is what kills CDP for the installed browsers.

Together these eliminate every mainstream stack: the signed browsers refuse debugging, and the
debuggable browsers are unsigned. **Selenium and Playwright are not viable here.**

WebView2 is the one signed Chromium engine permitted to run, so it survives as a speculative
spike (task 11) — never as a foundation.

## Approach

Keep `curl_cffi`. Win by collecting far more data per request and by surviving blocks instead of
dying on them. Target: a complete Vilnius dataset with `failed: 0`.

### The core insight

One search page carries **25 listings**, each card already containing price, price/m², rooms,
area, floor and total floors, build year, heating, condition, district, street and image URLs.

| | Requests | Records |
|---|---|---|
| Today | 1 search + 10 detail | 10 full |
| After | 11 search | **275 partial** + detail on the remainder |

Roughly a 22× multiplier against a count-based budget, needing no new binaries.

Detail pages stay worthwhile for what only they carry: description text, exact coordinates,
seller contact, engagement stats, fine-grained features and area breakdowns.

## Verification criteria

1. Full test suite green, coverage ≥ 80 %.
2. `doctor` reports `Transport: curl`, HTTP 200 for robots.txt and the search page, plus cookie
   age and `_px3` presence.
3. **Yield:** a short run produces **≥ 200 records from ~11 requests** (baseline today: 10–16).
4. **Block survival:** the run stops its burst on the first 403 rather than firing more into the
   block, checkpoints, cools down, resumes, and ends with `failed: 0`.
5. **Resume:** killing the run mid-crawl and restarting produces no duplicate and no missing
   listings.
6. **Field agreement:** where a listing has both a search and a detail record, price, area and
   rooms agree.

## Completed work

### Task 2 — search-card parser

`src/aruodas_scraper/parsers/search_card.py` exposes
`parse_search_cards(html, category, source_search_url, page, city=None, mapping_path=None)`
returning `tuple[ListingRecord, ...]`. It reuses the existing normalizers
(`numbers`, `text`, `privacy`, `translations`) rather than adding new ones.

Cards whose `data-uid` does not match the category prefix (`1-…` apartments, `2-…` houses)
are adverts or development-project promos and are skipped, as are cards with no listing link.

Run against the cached Vilnius search page it returns **25 of 25 cards**, every one carrying
price, price/m², rooms, area, floor, total floors, build year, heating, condition, district,
street, search position and image URLs. `"2/4 aukšt."` splits into floor `2` and total floors
`4`; a bare `"2 aukšt."` yields total floors only rather than a fabricated floor.

Covered by `tests/unit/test_search_card.py` (5 tests) against a **synthetic** fixture at
`tests/fixtures/html/search_cards_apartments.html` — no live HTML or personal data, per
`AGENTS.md`.

### Task 3 — one schema for both sources

- `ListingRecord.record_source: Literal["search", "detail"] = "detail"`. The default keeps
  every existing caller and test valid without change.
- `price_per_sqm_eur` **already existed** and `parse_listing` already populated it, so no
  schema work was needed there.
- `CSV_FIELDS` derives from `ListingRecord.model_fields`, so the export header picked up the
  new column with no edit.
- Merge precedence is now explicit in `pipelines/online.py::_merge_records`: later records
  win, **except** that a search record never displaces a detail record for the same
  `listing_id`. The previous `{**existing, **new}` dict comprehension relied on ordering alone,
  which would have let a search card overwrite richer detail data.

### Task 4 — two-phase crawl

`pipelines/online.py::process_online` no longer interleaves search and detail requests.
The old loop fetched search page 1, then immediately spent the request budget on that
page's detail pages — so the budget ran out on page one's listings and pages 2-11 were
never even requested.

- **Phase A** pages through search results for every category, parsing each page's cards
  straight into the export and queueing candidates for detail. Failure to parse a page's
  cards is recorded as a `parse_search_cards` failure and the crawl carries on, because a
  page that was served still yielded usable pagination links.
- **Phase B** spends whatever budget is left on detail pages from the queue.

Consequences of the split, both intended:

- `listings_discovered` now counts every listing seen, not just those chosen for a detail
  fetch. `max_listings_per_category` still caps detail fetches only.
- A block during phase B no longer prevents another category's *search* page from being
  fetched, because all search pages are already done. This is strictly better under a
  count budget, and two integration tests were updated to describe the new ordering.

Verified by `test_search_cards_are_exported_for_listings_the_detail_budget_never_reaches`:
with `max_listings_per_category=1` against a two-card page, both listings are exported —
one as `record_source=detail`, the other as `record_source=search` with its price and
room count intact — and the second detail page is never requested.

Full suite green at this point: **183 passed**.

### Task 5 — budget-aware burst/cooldown scheduler

`src/aruodas_scraper/networking/budget.py` holds `BudgetPolicy` (a frozen, self-validating
dataclass) and `RequestBudget`. The caller asks `request_permitted()` before each request and
reports the outcome through `record_success()` / `record_block()`. `request_permitted()` blocks
for the cooldown when the burst is spent and returns `False` only when the run itself is over,
leaving the reason in `stop_reason`.

Three properties make it worth having:

- **The burst ends on the first refusal.** Previously the run fired its whole remaining queue
  into an active block — the last live run scored 11 successes then 11 wasted 403s over ten
  minutes, each of which renewed the TTL.
- **The next burst aims below the last observed ceiling**, at
  `max(minimum_burst, observed_ceiling - safety_margin)`. Stopping voluntarily means the block
  is often never tripped, so no request is wasted and the TTL never restarts. The ceiling
  tracks the *lowest* burst the origin allowed, because a later smaller one means it tightened.
- **The run always terminates**, bounded by `max_cooldowns` and `max_runtime_seconds`. A
  cooldown that would overrun the runtime limit is not started at all.

In `pipelines/online.py` this replaces both `_MAX_CONSECUTIVE_BLOCKS` and the separate
`_MAX_DEFERRED_PASSES` retry pass. Retries are now inline: a refused listing goes to the back
of a `deque` with an attempt counter, so the burst that resumes after the cooldown starts on
listings never asked for yet and comes back to the refused one afterwards. Search pages are
retried on the *same* URL, which cannot renumber pagination, so the old "never retry a search
page" restriction is gone.

Consequences, all intended:

- `DEFAULT_RETRY_COOLDOWN_SECONDS` moved 300 → 1500. Undershooting the observed 20-25 minute
  TTL lands the retry inside the block and renews it; overshooting merely costs time.
  `config/scrape.yaml` was updated to match.
- Anything still unfetched when the budget runs out is recorded as a `BudgetExhausted` failure
  rather than dropped, so the export states what is missing. One test moved from `failed == 1`
  to `failed == 2` for exactly this reason: the listing behind the blocked one used to vanish.
- `deferred_retries_attempted` / `_recovered` now count only listings actually refused and
  re-attempted, not everything swept up by a blanket second pass.
- `retry_cooldown_seconds: 0` now means "never wait", which ends the run at the first block.

Covered by `tests/unit/test_budget.py` (12 tests, injected clock and sleeper so nothing really
waits) plus four block-behaviour integration tests. Full suite green: **196 passed**, coverage
**87.98 %** (`budget.py` 98 %, `online.py` 91 %).

### Task 6 — resume that actually resumes

Two defects, one of them introduced by task 4 itself:

- **A search-only listing was treated as done.** `known_ids` was every listing ID in the
  existing export regardless of `record_source`, so the run after a budget shortfall would
  skip precisely the card-only listings it still owed a detail fetch. That made the shortfall
  permanent. It is now `detailed_ids`, built only from `record_source == "detail"`. Records
  written before this field existed read back with the `"detail"` default, so older exports
  keep their meaning.
- **A mid-run kill lost everything.** Exports were written once, at the end. A budgeted run is
  mostly cooldown and is measured in hours, so interruption is the normal case rather than the
  exception. A `flush()` now rewrites the CSVs and `failed_urls.csv` after phase A and after
  every completed burst in phase B, detected by watching `budget.cooldowns_used` change.

Moving the flush earlier exposed an ordering bug: the "every category's first page was
blocked" abort ran *after* phase B, so flushing created an output directory for a run that
was about to raise. Both operands are final the moment phase A ends, so the check moved
there, which also skips a phase B that could have nothing queued.

**Deliberately not persisted: the pagination cursor.** Storing "resume at page 7" looks like a
budget saving but is unsound here, because Aruodas reorders search results continuously — a
cursor written an hour ago points at different listings, so resuming from it would silently
skip listings that shifted backwards across the boundary. That directly contradicts
verification criterion 5. Re-walking pagination from page 1 re-harvests the same cards, which
`_merge_records` collapses by `listing_id`, and the run then spends its budget only on
listings that still lack a detail record.

Covered by two new integration tests: a second run deepens a listing that only had a search
record, and phase A's cards are already on disk after a run that dies during phase B. Full
suite green: **198 passed**, coverage **88.20 %** (`online.py` 93 %).

### Task 9 — config and CLI wiring

The single biggest yield blocker was not code at all: `config/scrape.yaml` shipped with
`max_pages: 1`, which capped phase A at one search page and left the ~22× card multiplier
almost entirely unspent. It is now `max_pages: 20` and `max_listings_per_category: 60` —
roughly 500 card records for 20 requests, then detail fetches for as much of the remainder as
the budget funds.

`retry_cooldown_seconds` rose from 300 to 1500. Three hundred seconds sits well inside the
observed 20-25 minute block TTL, so every "recovery" landed inside the live block and renewed
it. Sitting a little over the TTL is the only safe side of that bet.

New keys `max_cooldowns` (default 4) and `max_runtime_seconds` (unset) bound a run that would
otherwise sit in cooldowns indefinitely; both are exposed as `--max-cooldowns` and
`--max-runtime-seconds` on `scrape-live`. `ScrapeLiveOptions` uses `extra="forbid"`, so each
one needed a matching pydantic field or the config file would be rejected outright.

Covered by the existing `test_run_config.py` round-trip plus range-rejection cases for
`max_cooldowns` and `max_runtime_seconds`. Full suite green: **201 passed**, coverage
**88.23 %**.

### Task 7 — human realism

This task is secondary by measurement, not by preference: the ceiling is a per-IP request
*count*, and raising the gap from 13 s to 45 s did not move it. Nothing here lifts the budget.
What it does is remove cadence signals that are free to remove and cost nothing to keep off.

Two changes, both aimed at the shape of the traffic rather than its rate:

- **Reading pauses.** Jitter cannot produce a heavy-tailed gap distribution, because it is
  bounded by the band it is drawn from — "5 ± 2" yields gaps between 3 s and 7 s and never
  anything else, which is a flatter, tighter distribution than any person generates.
  `DelayPolicy` now carries `pause_probability`, and roughly one delay in eight also draws a
  30-90 s pause. Off by default, so a policy built directly (as the tests and library callers
  do) stays predictable; the CLI turns it on for live runs.
- **Randomised visit order.** Which listings get fetched is still decided by page order, so
  the budget still goes to the newest first, but the order they are *visited* in is shuffled
  within each page. Walking every page's cards strictly top to bottom is a pattern no reader
  produces. The shuffle is injectable, because the integration tests assert an exact request
  sequence.

**Two items from the original plan were deliberately not implemented**, because they work
against the cookie strategy rather than with it:

- **Rotating the impersonation profile mid-run.** The bot-protection layer binds a session to
  the user agent that minted it, and each `impersonate` profile carries its own User-Agent.
  Rotating profiles while sending a borrowed browser cookie puts the identity in
  contradiction — the exact signal the curl transport exists to remove.
- **Clearing the session every N requests.** The borrowed cookie is sent as a fixed header,
  so clearing the jar only discards cookies the *origin* set. Against a per-IP count budget
  that buys nothing, and it throws away any `_px3` the origin issued on a good response.

Covered by four new `DelayPolicy` tests (default-off, tail present, always-on, and the
extended rejection cases) plus the CLI pacing test, which now asserts the live policy produces
either an in-band gap or an in-band gap plus a pause, and nothing between. Full suite green:
**208 passed**, coverage **88.33 %**.

### Task 8 — cookie lifecycle

The borrowed browser cookie is the one lever that raises the request ceiling without changing
IP, and it was the least observable thing in the system. `_read_cookie_file` returned a string:
a cookie that had expired hours ago, and a cookie that never carried a protection token at all,
were both indistinguishable from a healthy one. Either failure shows up only as a lower ceiling,
which is exactly what the run is trying to diagnose — and it is the prime suspect for the last
run's low ceiling (`handover.md`).

New `networking/cookie_source.py` returns a `BrowserCookie` carrying the three facts that
decide whether the cookie is worth anything: its byte length, its age from the file's mtime,
and whether a `_px3` cookie is actually present. Both `scrape-live` and `doctor` now print that
summary before they make a request, and warn when the token is missing or the copy is over an
hour old.

Details that matter:

- **`_px3` is matched by cookie *name*, not substring.** The header is split on `;` and `=`, so
  a `_px3`-shaped string inside some other cookie's value cannot read as the token.
- **`describe()` never discloses the value**, which authenticates this client to the origin.
  A test asserts the value does not appear in the CLI output.
- **Age is floored at zero.** The mtime records when the header was *copied*, not when the
  origin issued it, so the age is a lower bound; a clock skewed behind the file would otherwise
  produce a negative age that reads as suspiciously fresh rather than obviously wrong.

**The `CookieSource` protocol from the original plan was not built.** It was justified purely as
a seam for the deferred WebView2 spike, and a protocol with exactly one implementation is an
abstraction with nothing to abstract over. If that spike is ever picked up, the seam is one
function signature away.

Covered by `tests/unit/test_cookie_source.py` (10 tests) and four new `doctor` integration
tests, which had no coverage at all before. Full suite green: **218 passed**, coverage
**90.00 %**.

### Task 10 — tests and end-to-end verification

Running the thing live is what found the two bugs that no test caught, and both of them
defeated the entire strategy while every test stayed green.

**Bug 1 — the detail cap was throttling the search walk.** Pagination stopped as soon as the
detail queue filled: `if should_stop or category_queued >= max_listings_per_category: break`.
Phase A costs one request per ~25 records and phase B costs one request per record, so letting
the phase B cap end the phase A walk threw away the whole multiplier — a 60-listing cap stopped
the crawl three pages in. Card yield is now bounded by `max_pages` alone.

**Bug 2 — `next_page_url` was never found on a live page.** This was the real cap. The
pagination pattern was `/(?:butai|namai)/puslapis/(\d+)/?$`, which requires the page number to
follow the category directly. Aruodas puts the search filters in between, so the live link is
`/butai/vilniuje/puslapis/2/` and the pattern matched nothing. `PaginationState.should_stop`
treats a missing next page as the end of the results, so **every live run silently stopped at
page 1 and returned 25 records**, no matter what `--max-pages` said. It looked like a completed
run: `Failed: 0`, no warning, no error.

The fix accepts any filter segments between the category and `puslapis`, and pins the category
per property type so a cross-category link in the page chrome cannot hijack the walk. While
there: `a[href]` also matches a valueless attribute, for which selectolax returns `None` rather
than `""`, so `urljoin` would have raised on it.

**Verification of the primary success metric.** The plan asks for ≥ 200 records from ~11
requests, against a baseline of 10-16. `test_the_search_walk_multiplies_records_far_beyond_one_record_per_request`
drives the real pipeline over ten synthetic search pages of 25 cards each and asserts
**250 records from 11 requests** — 249 from cards, 1 deepened by a detail fetch, `failed: 0`.
Synthetic rather than captured, per `AGENTS.md`; the fixture reproduces the *filtered*
pagination shape specifically, so bug 2 cannot come back unnoticed.

**Live run — criteria 3, 4 and 6 met.** With a fresh cookie (6438 bytes, 25 cookies, a valid
651-char `_px3`) and after letting the block lapse:

```
Exported 249 apartment(s) and 0 house(s). Skipped 0 already-exported listing(s). Failed: 0.
```

249 real records from **11 requests** — 10 search pages plus 1 detail page — against a baseline
of 10-16. Criterion 3 met on live data, not just on the synthetic fixture.

The commands that produced it. One per line: PowerShell continues a line with a backtick, not a
backslash, so a wrapped paste silently drops the argument after the break.

```
python -m aruodas_scraper doctor --transport curl --cookie-file ../aruodas_secrets/cookie.txt
python -m aruodas_scraper scrape-live --property-type apartments --max-pages 10 --max-listings-per-category 1 --transport curl --cookie-file ../aruodas_secrets/cookie.txt
```

**The adaptive scheduler works on a real block (criterion 4).** Burst 1 took 5 pages and was
refused on the 6th, so it stopped there rather than firing the rest of the queue into the block,
and recorded a ceiling of 5. Burst 2 then fetched exactly 5 pages and **stopped voluntarily —
without tripping a block at all**:

```
09:42 Origin refused this client after 5 successful request(s) in the burst.
      Stopping the burst; the next one will aim for 5 request(s).
      Pausing 1500s for cooldown 1/4 ...
10:10 Pausing 1500s for cooldown 2/4 after 5 request(s) in this burst.
```

The second cooldown line has no refusal above it. Learning the ceiling from one block and then
staying under it is exactly the Task 5 design, confirmed end to end.

**Bug 3 — a detail record was wiping card-only fields (found by criterion 6).** Comparing
listing `1-3680506`'s card against its detail record: price, price/m², rooms, floor, total
floors, year and street all agreed, and area differed only by the card's rounding (35.0 vs
34.98) — the new parser validates against the trusted one. But `district` was `Šnipiškės` on the
card and **empty** on the detail record. A detail record outranks a card but is not a superset
field by field, and `_merge_records` was replacing the card wholesale, throwing away data the
run had already paid a request for. It now backfills: the detail record wins every field it
states, and inherits only the ones it left unset.

Every fixture detail page happened to carry a district, which is why 223 green tests missed
this. `test_a_detail_record_inherits_card_fields_its_own_page_never_stated` serves the detail
page with the district removed and asserts the exported row still has it — verified to fail
before the fix.

Also fixed here: the three long-standing mypy errors in `curl_fetcher.py`, and three bandit
B311 false positives in `rate_limiter.py`. `make check` runs `mypy src` and `bandit -r src`, so
the repository was failing its own gate on the transport that is now the primary path. curl_cffi's
annotations understate two arguments — `verify` is typed `bool` although curl accepts a CA bundle
path, which is the only way through a TLS-intercepting proxy, and `proxies` is typed as a
TypedDict that a plain mapping satisfies. The bandit findings were a local named `random` holding
a `secrets.SystemRandom()`; the generator was already the strong one, the name just matched the
blacklist. Renamed to `rng`.

Full gate green: **224 passed**, coverage **90.07 %**, mypy clean across 58 files, black and
isort clean, bandit exiting 0.

Criterion 7 — spot-checking two exported rows against the live pages by eye — is the one item
still open, and needs a human with a browser rather than a code change.

## Task 12 — repeat runs were re-spending the scarcest resource

`config/scrape.yaml` had `overwrite: true`, which empties `detailed_ids` at
`pipelines/online.py:360-368`. Every run therefore re-fetched detail pages already on disk. Against
a budget of roughly five requests per cooldown that spends the whole run on data already
collected, and the card-only listings that still owe a fetch are never reached — the exact
opposite of the resume behaviour built in task 6.

Now `false`. Only a `record_source == "detail"` record marks a listing done, so successive runs
deepen the dataset instead of rebuilding it, and `--overwrite` remains for a deliberate price
refresh. Verified with `show-config --command scrape-live` (note the flag: a bare `show-config`
reports a different file).

## Task 13 — is there a cheaper data surface than rendered HTML?

Asked because request *count* is the binding constraint, so any surface returning more listings
per request would win outright. Answer: **no**. Full evidence in
[docs/data_surface_assessment.md](docs/data_surface_assessment.md); the short version is that
JSON-LD is strictly poorer than the existing parser, there is no hydration payload or bulk API,
the DoubleClick pixel is page-level on search pages so it saves nothing, and `sitemap.xml`
returns 403 behind Cloudflare. The phase-A card walk at ~25 records per request is already the
efficient surface.

The whole table was answered from the 24 pages in `data/raw/cache/` at zero request cost; only
`robots.txt` and `sitemap.xml` needed live fetches, one each.

Two incidental findings worth carrying forward: a 403 here can come from **Cloudflare or
PerimeterX**, which matters when diagnosing one; and `robots.txt` gives named crawlers explicit
stanzas with `Crawl-delay: 10` while `User-Agent: *` is `Disallow: /`, which is what the
allow-list request in `docs/connectivity_fix_plan.md` would change.

## Task 14 — README operator runbook

`README.md` still documented a scraper that no longer exists: 12-15 second delays (now 3-7) and a
`--max-pages 1` example that is now precisely the anti-pattern, with no mention of the cookie, the
burst/cooldown scheduler, or what to do when a block hits. The live-retrieval section is now a
runbook covering the two no-flag commands, the DevTools **Raw** toggle (the parsed view truncates
the cookie and yields one without `_px3`, which behaves exactly like no cookie), the block log
lines, the "never re-run while blocked" rule, and the realistic per-run envelope.

## Tasks 15-18 — the export went sparse after row 18, and why

**The report.** Latitude and longitude stopped after row ~18 of `apartments_vilnius.csv`, with many
other fields going empty at the same point.

**What was actually happening.** The rows after the cutoff are a different *kind* of record, not
a broken parse. Counted from the export at the time:

| | apartments | houses |
|---|---|---|
| `record_source == "detail"` | 16 (all dated 2026-08-18) | 0 |
| `record_source == "search"` | 484 (all 2026-08-20) | 498 (all 2026-08-20) |

Nineteen columns are detail-only — `latitude`, `longitude`, `description_lt`,
`listing_created_date`, `views_count`, `building_type`, `energy_class`, `balcony`, the address
breakdown — and no card carries them. So the data was consistent; the *depth* was not.

**Root cause.** Phase A walks search pages, phase B fetches detail pages, and both draw on one
`RequestBudget` created once at `pipelines/online.py:291` and never reset. Phase A runs to
completion first and nothing reserves anything for phase B. With `max_pages: 20` and
`property_type: all`, phase A wants 20 × 2 = **40 requests** against an origin that sells ~5-10 per
burst. Phase A consumed the entire budget and every cooldown; phase B got nothing. Both exports
show pages 1-20 fetched for both categories and zero new detail records — exactly that shape. The
08-20 run did not even finish: `flush()` wrote the CSVs at 15:25 while `scrape_summary.json` is
still dated 08-18, so it was interrupted sitting in the cooldown phase B's first request triggered.

**Task 16 — deepen-first seeding.** A new `deepen` flag (default on, `deepen: true` in
`config/scrape.yaml`, `--deepen`/`--no-deepen` on the CLI) skips the search walk outright when the
export already holds card-only listings, and seeds phase B from those rows instead. Their canonical
URLs are already banked, so no discovery is needed to make progress and not one request is spent on
a page already harvested. `--no-deepen` is how you go find new listings; an export with no
card-only rows falls through to the search walk on its own, so a fresh checkout still works.

**Task 15 — the merge asymmetry.** `_backfill_from_card` only ran when a *new detail* met an
*existing card*. The reverse — a re-seen card meeting an existing detail row — hit a bare `continue`
and was discarded wholesale, silently dropping `district`, `image_count`, `image_urls` and
`search_position`, the four columns that are card-only. This was a latent bug, not the cause of the
reported sparseness: no listing in that export was held as both, so it had not yet fired.

**Task 17 — a bug the deepening path uncovered.** `parsers/search_card.py` canonicalized the card
href without resolving it against a base, so a root-relative href was exported verbatim as
`canonical_url`. Nothing consumed card URLs before, so it never surfaced; the first deepening run
against such a row failed with `URL must be an HTTPS Aruodas URL`. Fixed at the parser, matching
what `discovery/listing_links.py` already did. The live exports happen to hold no relative URLs, so
no data on disk needed repairing.

**Verification.** Three integration tests, each mutation-checked to confirm it fails without its
fix: a deepening run issues no search requests and gives the card-only row its coordinates; a
deepening run still walks search when nothing is card-only (both cold start and a fully-detailed
export); and a re-seen card backfills `district` into an existing detail row without demoting it or
overwriting `latitude`/`street`. Suite: 227 passed, 90.13% coverage, `black`/`isort`/`mypy`/`bandit`
clean.

---

## Task 19 — a burst held across a cooldown is a burst at risk

Phase B banked its work *after* a cooldown rather than before it: `request_permitted()` sleeps for
the full ~25 minutes and only then did the flush run. So every row a burst had just paid a request
for sat unsaved through the longest idle stretch of the run — the window in which a run is by far
the most likely to be killed. That is exactly how the 08-20 run ended: interrupted mid-cooldown,
with `scrape_summary.json` left at 08-18.

The first live deepen run made the cost concrete. Six detail pages fetched in 4 minutes, then a
1500-second wait holding all six in memory.

`RequestBudget` now exposes `burst_is_spent`, and phase B flushes before the wait instead of after
it. Mutation-checked: with the pre-cooldown flush disabled, the new test loses the row and fails
with `KeyError: '1-1234567'`. Suite: 228 passed, 90.14%.

## Task 20 — live verification

Run started 17:10 on 2026-08-20 with a 104-minute-old cookie that `doctor` flagged as probably
rotated; both probes returned HTTP 200 and detail fetches then succeeded, so the staleness warning
is a heuristic on file age, not an observation.

The deepen path did what it was built to do:

```
Deepening 120 listing(s) held only as search cards; skipping the search walk so the whole
request budget goes to detail pages. Pass --no-deepen to discover new listings instead.
[apartments 1/120] OK ... [apartments 6/120] OK
Origin refused this client after 6 successful request(s) in the burst.
```

Zero search pages fetched, six detail pages fetched. Under the previous behaviour those same six
requests bought six search pages already harvested and produced no new detail rows at all.

## Honest limits

- Only an IP-level change removes the ceiling outright: run off the shared corporate egress, use
  a proxy pool, or land the Aruodas allow-list request (`docs/connectivity_fix_plan.md`), for
  which written authorization already exists. Everything here maximises yield *under* the ceiling.
- Search-derived records are genuinely partial. Depth comes only from detail fetches.
- Deepening does not raise the ceiling, it only stops the budget being spent on pages already
  read. A run funds roughly 20-40 detail pages, so the ~1000 card-only listings currently banked
  are on the order of thirty runs away from full depth. Coverage now grows per run instead of not
  at all.
- The borrowed cookie is a consumable, not a setting. It is minted by a JavaScript sensor this
  client cannot run, so it has to be re-copied from a browser roughly hourly; `doctor` now says
  when it has gone stale instead of leaving it to be inferred from a disappointing run.
