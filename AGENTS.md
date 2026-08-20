# AGENTS.md
_Last reconciled: 2025-07-15_

## Overview
- Scraper and parser for **aruodas.lt** (Lithuanian real-estate portal), producing normalized English CSV datasets of apartments and houses.
- Two modes: **online** (live retrieval via `scrape-live`) and **offline** (parse local HTML via `parse-offline`). Both export to `data/processed/`.

## Architecture
- **Entry point**: `src/aruodas_scraper/cli.py` — Typer app exposed as `aruodas` console script; subcommands: `parse-offline`, `scrape-live`, `doctor`, `validate`, `report-unknown-fields`, `show-config`.
- **`__main__.py`** enables `python -m aruodas_scraper`.
- **`run_config.py`** loads `config/scrape.yaml` (the default run configuration); merges with CLI flags (CLI wins).
- **`config.py`** loads a legacy offline-only YAML (`config/default.yaml`); used by `OfflinePipeline`.
- **`cities.py`** loads `config/cities.yaml` — registry of city/category definitions (search URLs, listing-ID prefixes, output filenames).
- **Pipelines**: `pipelines/online.py` (live), `pipelines/all_properties.py` (offline orchestration), `pipelines/offline.py` (snapshot loader), `pipelines/export.py` (atomic CSV/JSON writes).
- **Networking**: `networking/http_client.py` (AruodasHttpClient — caching, retries, redirect following, URL allowlist), `networking/curl_fetcher.py` (CurlCffiFetcher — Chrome TLS fingerprint via curl_cffi), `networking/cache.py` (SHA-256 content-addressed HTML cache in `data/raw/cache/`).
- **Parsers**: `parsers/common.py` (`parse_listing` — shared detail extraction), `parsers/apartment.py`, `parsers/house.py`, `parsers/coordinates.py`, `parsers/engagement.py`, `parsers/structured_data.py` (JSON-LD).
- **Discovery**: `discovery/listing_links.py` extracts listing URLs from search pages; `discovery/pagination.py` tracks pagination state.
- **Normalization**: `normalization/translations.py` loads `config/field_mappings_lt_en.yaml` (Lithuanian→English field/category/feature maps); falls back to a packaged copy in `src/aruodas_scraper/resources/`.
- **Validation**: `validation/records.py` (CSV duplicate check), `validation/quality_report.py` (missingness/duplicate metrics), `validation/coordinates.py` (Lithuania bounding box).
- **Models**: `models.py` — Pydantic v2 frozen models: `ListingRecord`, `DiscoveryRecord`, `FailedUrl`, `ScrapeSummary`, `OnlineScrapeSummary`, `UnknownField`.

## Build & run
- Python ≥ 3.12 required.
- `make install` — installs dev deps + editable package.
- `make test` — runs pytest (unit + integration); `make coverage` enforces ≥ 80 % branch coverage.
- `make lint` — black + isort check; `make format` — auto-format; `make typecheck` — mypy strict; `make security` — bandit.
- `make check` — runs lint, typecheck, security, coverage, and build in sequence.
- CI: `.github/workflows/ci.yml` runs on every push/PR; also installs `ruff==0.12.9` for linting.
- Live scrape workflow: `.github/workflows/scrape-live.yml` — manual dispatch or daily cron (04:17 UTC).
- Run live: `python -m aruodas_scraper scrape-live` (reads `config/scrape.yaml` by default).
- Run offline: `python -m aruodas_scraper parse-offline --input <dir>`.

## Conventions
- All file writes are **atomic** (write to `.tmp`, then `os.replace`).
- CSV export neutralizes spreadsheet formula injection (prefixes `=`, `+`, `-`, `@` with `'`).
- URL fields (`listing_url`, `canonical_url`) are placed last in CSV column order.
- The HTTP client **only allows HTTPS requests to `aruodas.lt` / `www.aruodas.lt`** — anything else raises `RetrievalError`.
- Rate limiting defaults: 10 s minimum + 2–5 s random jitter between every request.
- Offline checkpoints use SHA-256 content digests per file; stored in `data/interim/checkpoints/`.
- Tests are marked `@pytest.mark.unit` or `@pytest.mark.integration`; fixtures live in `tests/fixtures/`.
- `ListingRecord` uses `extra="forbid"` — any unmapped field causes a validation error.
- Field mappings YAML is validated against `ListingRecord.model_fields` at load time.
- The `curl` transport is the production default; `httpx` is kept for tests/offline only (detectable fingerprint).
- Default impersonation profile: `chrome131`; newer profiles (e.g. chrome136) are rejected by the origin.

## Auth & security
- **Cookie file** (`cookie_file` in `config/scrape.yaml`): path to a file containing a browser Cookie header; kept outside the repo. Max 16 KiB.
- **CA bundle** (`ca_bundle`): for TLS-intercepting corporate proxies (e.g. Zscaler); also respects `SSL_CERT_FILE`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE` env vars.
- **Proxy**: explicit `proxy` URL (with credentials) for corporate environments.
- `config/scrape.yaml` references `../../aruodas_secrets/cookie.txt` — never committed.
- Max response size: 10 MiB (`MAX_HTML_FILE_BYTES`); max HTML files per offline run: 10 000.
- Symlink HTML inputs are rejected in offline mode.
- Contact details (phone numbers, emails) are redacted from descriptions via `normalization/privacy.py`.

## Gotchas / notes
- Bot protection (PerimeterX) scores TLS fingerprint, HTTP/2 SETTINGS, and header order — not just User-Agent. Only the `curl` transport passes.
- `px-captcha` in a 200 response body is treated as a block (challenge page); the session is cleared before retry.
- Blocked retries use a separate budget: 2 attempts with 30 s backoff (vs. 3 attempts / exponential for transient errors).
- `FetchOptions.transport` defaults to `"httpx"` in code, but the CLI and `config/scrape.yaml` override to `"curl"`.
- Online pipeline merges new records with existing CSV (keyed by `listing_id`); never removes rows absent from the current run.
- Online pipeline abandons the run after 3 consecutive blocks (`_MAX_CONSECUTIVE_BLOCKS`), exporting what was collected so far.
- Offline `--resume` requires the exported CSV to still exist; otherwise raises `CheckpointError`.
- The `_classify` function in `all_properties.py` routes HTML to apartment vs. house parser by inspecting the canonical URL path (`/butai-` + `1-` → apartments, `/namai-` + `2-` → houses).
- `config/scrape.yaml` is the default run config (`DEFAULT_RUN_CONFIG`); `config/default.yaml` is a separate legacy settings file for `load_settings`.
- `DelayPolicy` (networking/rate_limiter.py) is `@dataclass(frozen=True, slots=True)`, so `DelayPolicy.minimum_seconds` read off the *class* is a slot descriptor, NOT 10.0. A module constant `DEFAULT_MINIMUM_DELAY_SECONDS = 10.0` is exported for use as CLI/typer defaults. Never read a default off a slotted dataclass class object.
- `.github/workflows/scrape-live.yml` must pass `--no-config`: the CLI auto-discovers `config/scrape.yaml`, whose `cookie_file` only exists on the local workstation, so CI would abort with "Cookie file was not found".
- `browser_profile._CHROME_MAJOR_VERSION` is deliberately `"131"` to stay in step with `curl_fetcher.DEFAULT_IMPERSONATION`; do not bump independently.
- Key dependencies pinned in `pyproject.toml`: `curl_cffi==0.16.0`, `httpx==0.28.1`, `pydantic==2.11.7`, `selectolax==0.3.29`, `tenacity==9.1.2`, `typer==0.16.0`.
- `config/scrape.yaml` sets `min_delay_seconds: 45.0` and `overwrite: true` — tuned for the per-IP request budget (~11 requests before block).
- (2026-08-18) Aruodas per-IP ceiling is a request COUNT, not a rate: a run paced at min_delay_seconds=45 still collected only 16 detail pages before 403s began, barely more than the ~11 seen at ~13s. Slower pacing therefore buys almost nothing; config/scrape.yaml was lowered to min_delay_seconds: 5.0 + jitter_seconds: 2.0 and recovery moved to the deferred retry pass.
- (2026-08-18) Symmetric request pacing is expressed via DelayPolicy.centred(center, jitter) (networking/rate_limiter.py), NOT by constructing DelayPolicy directly: wait() is additive (minimum + uniform(random_min, random_max)) and validate() forbids negative bounds, so "5 +/- 2" must be encoded as minimum=3 with a draw from [0, 4]. centred() raises ValueError when jitter > center. DEFAULT_JITTER_SECONDS = 2.0 is a module constant for the same slotted-dataclass reason as DEFAULT_MINIMUM_DELAY_SECONDS. The CLI also rejects --jitter-seconds > --min-delay-seconds with a BadParameter.
- (2026-08-20) Phase A and phase B share ONE RequestBudget that is never reset, and phase A runs to completion first. With max_pages=20 x 2 categories it wants 40 requests against a ~5-10 request burst, so it consumed the whole budget and phase B fetched nothing — that is why exports filled with card rows carrying no latitude/longitude. `deepen: true` (config/scrape.yaml, `--deepen/--no-deepen`) now seeds phase B from card-only rows already in the export and skips the search walk entirely. It falls back to the search walk on its own when no row is card-only, so a fresh checkout still works; `--no-deepen` is how you go discover new listings.
- (2026-08-20) Phase B flushes BEFORE the cooldown, not after: `request_permitted()` sleeps for the full ~25 minutes internally, so a post-cooldown flush left a whole burst of detail rows unsaved across the window where a run is most likely to be killed (which is how the 08-20 run lost its phase B). `RequestBudget.burst_is_spent` exists for this — it is the public read of the private `_burst_is_spent()`.
- (2026-08-20) parsers/search_card.py must resolve card hrefs against `_BASE_URL` before storing `canonical_url`. Cards can carry a root-relative href, and nothing consumed card URLs until the deepening path did, so exporting one verbatim only fails later with "URL must be an HTTPS Aruodas URL".
- (2026-08-18) pipelines/online.py runs ONE deferred retry pass (DEFAULT_RETRY_COOLDOWN_SECONDS = 300.0, public because the CLI uses it as an option default; retry_cooldown_seconds=0 disables). Listings that got BlockedError, plus listings never attempted because _MAX_CONSECUTIVE_BLOCKS abandoned the main pass, are queued as _DeferredListing and re-run through the shared _attempt_listing() helper after sleeper(cooldown). Recovered listings are removed from failed_urls.csv, so OnlineScrapeSummary.failed counts only post-retry failures; deferred_retries_attempted/_recovered report the pass. Blocked SEARCH pages are deliberately not retried (re-discovery would renumber pagination). process_online takes an injectable `sleeper` so tests never really wait; the _run helper in tests/integration/test_online_pipeline.py defaults retry_cooldown_seconds=0.0 so main-traversal tests assert exact request counts.
