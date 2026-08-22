# Aruodas Listing Parser

A production-oriented Python 3.12 package for parsing Aruodas.lt apartment and house listing HTML
from local files into normalized English CSV datasets.

## Features

- Separate Vilnius apartment and house exports.
- Dynamic Lithuanian attribute capture with YAML-driven English field mappings.
- Raw preservation of every displayed property label and value.
- Correct separation of house floor area from land/plot area.
- Lithuanian number, date, percentage, and area-unit normalization.
- Primary-listing scoping that excludes ads, financing, furniture, navigation, and recommendations.
- Listing views and saved-user metrics scoped to the genuine listing.
- Coordinate extraction from embedded scripts with Lithuania bounds validation.
- Null-safe Pydantic models: missing data is never replaced by fabricated zero values.
- Atomic CSV/JSON writes, checkpoints, resume support, failure reports, and quality reports.
- Optional translation provider interface; disabled by default and no paid API required.
- Synthetic tests only: production listing content and personal contact details are not committed.
- GitHub Actions quality checks without live Aruodas requests.

## Installation

### Windows PowerShell

```powershell
C:\Program Files\Python312\python.exe -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m pytest
```

Use `python -m pip`, not a generated `pip.exe`, in environments where application control blocks
unsigned launcher executables.

### Linux or macOS

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip install -e .
.venv/bin/python -m pytest
```

## Offline input

Place listing **detail pages** as UTF-8 `.html` files in a local directory, for example:

```text
data/raw/
	apartment_1-1234567.html
	apartment_1-7654321.html
	house_2-1795101.html
```

Filenames are not used to infer property data. The parser classifies the page using its canonical
listing URL and reads attributes only from the primary listing container. Search-result pages can
be analyzed by the discovery parser, but `parse-offline` exports detail pages.

Do not place HTML containing personal contact information under version control. Runtime HTML,
outputs, checkpoints, and logs are ignored by `.gitignore`.

## CLI

### Ways to run it

| Command | Data source | Key options | What it does |
|---|---|---|---|
| `parse-offline` | Local HTML files (no network) | `--input`, `--property-type {apartments,houses,all}`, `--city`, `--output`, `--checkpoint`, `--resume`, `--refresh`, `--translate`, `--config`, `--no-config` | Parses already-downloaded listing-detail pages from a directory. `--property-type all` auto-classifies each file by its canonical URL (apartment vs house). `--input` is required unless the run configuration supplies it. |
| `scrape-live` | Live aruodas.lt (rate-limited, cached) | `--cities-config`, `--city`, `--property-type {apartments,houses,all}`, `--output`, `--cache`, `--max-pages` (1-500), `--max-listings-per-category` (1-20000), `--timeout-seconds` (1-120), `--solve-on-block`, `--refresh-cache`, `--overwrite`, `--deepen`/`--no-deepen`, `--config`, `--no-config` | Follows configured search pages and retrieves bounded listing-detail pages, producing the same normalized CSV/JSON as offline parsing. Deepens listings already held as search cards by default; `--no-deepen` walks the search pages to discover new ones. |
| `mint-cookie` | A real Chrome window | `--output`, `--url`, `--profile-dir`, `--timeout-seconds` (10-1800), `--config`, `--no-config` | Opens Chrome, waits for you to solve any bot-protection challenge, then writes the session cookie to `cookie_file`. A solved challenge is what raises the request budget, so this is the fix for a run that dies after ~6 requests. Needs the optional `playwright` extra. |
| `validate <csv_path>` | An exported CSV | — | Checks a CSV for duplicate listing IDs and reports total valid records. |
| `report-unknown-fields` | `data/processed/unknown_fields.csv` | `--report` | Prints any Lithuanian attribute labels seen that aren't yet mapped in `field_mappings_lt_en.yaml`. |
| `show-config` | `config/default.yaml` or the run configuration | `--config`, `--command {scrape-live,parse-offline}` | Displays the effective YAML configuration. With `--command`, prints the merged settings a run would actually use. |

The `--property-type` option selects which parser handles each listing: `apartments` → `parse_apartment()` (adds `apartment_total_area_sqm`, `rooms`, `floor`/`total_floors`); `houses` → `parse_house()` (adds `house_total_area_sqm`, `plot_area_*`, `number_of_floors`). Both are thin wrappers around the same shared extraction logic in `parsers/common.py` — only the derived fields differ, not how price/coordinates/description/etc. are extracted.

### Run configuration file

Every option above can also be set in `config/scrape.yaml`, which is loaded automatically when it
exists. Precedence is **explicit CLI flag > run configuration > command default**, so a flag you
actually type always wins. Point at a different file with `--config PATH`, or ignore the file
entirely with `--no-config`. Relative paths inside the file resolve against the file's own
directory.

```powershell
# Uses config/scrape.yaml for everything
.venv\Scripts\python.exe -m aruodas_scraper scrape-live

# Same, but override just one value
.venv\Scripts\python.exe -m aruodas_scraper scrape-live --max-listings-per-category 5

# See exactly what a run would use
.venv\Scripts\python.exe -m aruodas_scraper show-config --command scrape-live
```

#### Choosing the default command

Each section takes an `enabled` flag that selects which command runs when the CLI is invoked with
**no subcommand at all**. Exactly one may be enabled — setting both to `true`, or both to `false`,
is rejected with a clear error.

```yaml
scrape_live:
  enabled: true
parse_offline:
  enabled: false
```

```powershell
# Runs scrape-live because it is the enabled command
.venv\Scripts\python.exe -m aruodas_scraper
```

If neither section sets `enabled`, a bare invocation just prints the help text.

### Two tiers of record, and how to tell them apart

Every exported row comes from one of two places, and the **`record_source`** column says which:

| `record_source` | Cost | Fills |
| --- | --- | --- |
| `search` | ~1/25th of a request — one search page carries 25 cards | ~32 of 102 columns |
| `detail` | one whole request | ~45 of 102 columns |

A sparse row is therefore **not corrupt — it is just not deepened yet**. Nineteen columns are
detail-only, and they are the ones most likely to be missed: `latitude`, `longitude`,
`description_lt`, `listing_created_date`, `views_count`, `building_type`, `energy_class`,
`balcony`, `full_address` and the rest of the address breakdown. If coordinates stop partway down
the CSV, sort by `record_source` before concluding anything is broken.

The traffic is not one-way. Four columns are *card*-only — `district`, `search_position`,
`image_count` and `image_urls` — because no detail page states them. A card that arrives after a
detail row for the same listing therefore backfills those gaps instead of being discarded, and it
never demotes the row or overwrites a value the detail page did state.

### Re-running `scrape-live` over an existing export

Repeat runs are **additive**: each one spends its request budget on listings the previous runs
never reached, and rows that were not rediscovered are never deleted.

Only a **detail** record marks a listing as done. A search-card record is a strict subset of one,
so a listing that has only a card is still owed a fetch and will be revisited — otherwise the
first run's budget shortfall would become permanent. This is why successive runs deepen the
dataset rather than rebuilding it.

#### Deepen, then discover

Both phases of a run draw on **one** per-IP request budget, and the search walk is greedy:
`--max-pages 20` across two categories wants 40 requests, which is more than an entire run gets.
Left unchecked it consumes the lot and the detail phase never fetches anything — which is exactly
why rows kept arriving without coordinates.

`--deepen` (the default, and `deepen: true` in `config/scrape.yaml`) fixes that by inverting the
order of business. When the export already holds card-only listings, the search walk is **skipped
entirely** — zero requests on pages already harvested — and the whole budget goes to detail pages
for listings whose canonical URLs are already banked. The log says so on the way in:

```
Deepening 982 listing(s) held only as search cards; skipping the search walk so the whole
request budget goes to detail pages. Pass --no-deepen to discover new listings instead.
```

- Pass **`--no-deepen`** to go find *new* listings instead. That is the other half of the cycle:
  discover broadly with `--no-deepen`, then run the default repeatedly to fill the rows in.
- Cold start is automatic. An export with no card-only listings — a fresh checkout, or one where
  every row is already detailed — falls through to the search walk on its own.
- `--overwrite` implies discovery, so it takes the search walk regardless.

Be realistic about the rate. A run funds roughly 20-40 detail pages, so ~1000 card-only listings
is on the order of thirty runs. Deepening does not raise that ceiling; it stops the budget being
spent on pages already read.

- `--overwrite` re-fetches known listings and replaces their rows. Use it to refresh prices and
  listing status, not routinely: a run gets only about five requests per cooldown, so re-fetching
  a page already on disk costs a listing that has nothing yet.
- `--overwrite` on its own may still be served from the local HTML cache. To force genuinely fresh
  data, combine both: `--overwrite --refresh-cache`.
- The per-run `--max-listings-per-category` budget caps *detail* fetches only. Every listing seen
  while paging through search results is exported from its card regardless.
- If the existing CSV cannot be read, the run fails with a clear error rather than silently
  re-scraping everything. Pass `--overwrite` to ignore it.

Show all commands:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper --help
```

Parse both categories:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper parse-offline `
	--input data/raw `
	--property-type all `
	--city vilnius `
	--output data/processed `
	--checkpoint data/interim/checkpoints/vilnius.json
```

Parse one category:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper parse-offline --input data/raw --property-type apartments
.venv\Scripts\python.exe -m aruodas_scraper parse-offline --input data/raw --property-type houses
```

Resume or intentionally reprocess:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper parse-offline --input data/raw --property-type all --resume
.venv\Scripts\python.exe -m aruodas_scraper parse-offline --input data/raw --property-type all --refresh
```

Validate exports and inspect diagnostics:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper validate data/processed/apartments_vilnius.csv
.venv\Scripts\python.exe -m aruodas_scraper validate data/processed/houses_vilnius.csv
.venv\Scripts\python.exe -m aruodas_scraper report-unknown-fields
.venv\Scripts\python.exe -m aruodas_scraper show-config
```

## Live retrieval

### Running it

Two commands, no flags, from the repository root. `config/scrape.yaml` supplies everything —
city, limits, transport, and the cookie path — and `scrape_live.enabled: true` makes the bare
invocation run the scrape.

```powershell
.venv\Scripts\python.exe -m aruodas_scraper doctor
.venv\Scripts\python.exe -m aruodas_scraper
```

Write each command on **one line**. PowerShell continues a line with a backtick, not a
backslash, and a wrapped paste silently drops whatever follows the break.

Run `doctor` first. It reports the transport, the resolved TLS trust, whether Aruodas answers,
and — the part worth reading — the age of your cookie and whether it still carries a `_px3`.

### The cookie, and why it decides everything

Aruodas is behind PerimeterX. The `_px3` cookie that raises your request ceiling is minted by a
JavaScript sensor this client cannot execute, so the scraper borrows one from a real browser.

**How the cookie was earned matters more than how fresh it is.** Measured 2026-08-22: a cookie
copied from an ordinary browse was spent after about **6** requests, while a cookie taken after
the challenge was solved by hand carried a run past **100** without a refusal. A solved challenge
is scored as evidence of a human, and the budget is raised to match. Both cookies contain `_px3`,
so nothing in the file tells them apart — only how you got it.

Keep it **outside** the repository, at `../aruodas_secrets/cookie.txt`. `.gitignore` blocks
`aruodas_secrets/` and `*cookie*.txt` so a stray copy cannot be committed.

#### Refreshing it

```powershell
.venv\Scripts\python.exe -m aruodas_scraper mint-cookie
```

This opens Chrome, loads a search page, and waits. If a challenge appears, solve it in the window
— the command notices the page clear, harvests the cookie, and writes it. The value is never
printed. A persistent profile beside the cookie remembers previous solves, so most runs of this
command need no interaction at all.

It needs Playwright, which is an optional extra because nothing else uses it:

```powershell
.venv\Scripts\python.exe -m pip install playwright
```

No browser download follows; it drives the Chrome already installed.

<details>
<summary>Doing it by hand instead</summary>

Open aruodas.lt in a signed-in browser, DevTools → **Network** → click the document request →
**Headers** → switch on the **Raw** toggle → copy the entire `Cookie:` value into the file. The
Raw toggle matters: the parsed view truncates long values behind a `Show more` button, and
copying from it yields a cookie without `_px3`, which behaves exactly like no cookie at all.

</details>

The cookie goes stale after about an hour. `doctor` warns you rather than leaving it to be
inferred from a disappointing run.

### What a block looks like, and what to do

Nothing. It handles itself. The origin allows a finite number of requests **per source IP**,
then returns 403 for 20-25 minutes. On the first refusal the run stops the burst rather than
firing the rest of the queue into the block, remembers how far it got, waits out the cooldown,
and resumes just below the ceiling it observed:

```
Origin refused this client after 5 successful request(s) in the burst.
Stopping the burst; the next one will aim for 5 request(s).
Pausing 1500s for cooldown 1/4 after 5 request(s) in this burst. The block is per source IP
and self-clearing, so waiting is the only thing that restores it.
```

Exports are flushed after **every** burst, so killing the run costs nothing already collected.

The one thing that actively hurts is **re-running while blocked**: requests made inside a block
are refused *and* renew its TTL. If the run ends with `the origin is still refusing this client
after 4 cooldown(s)`, do not just wait it out — run `mint-cookie` and solve the challenge. That
clears the state immediately and is what restores the ceiling; waiting alone gives you back the
same ~6-request budget you just spent.

### Choosing what a run does

Deepening and discovery compete for one request budget, so a run does one or the other. With
`ask_phase: true` (or `--ask-phase`) the run asks at the start:

```
  Known so far: 998 publication(s), 828 still without details.

  [1] Add details to 828 listing(s) you already found.
      Description, coordinates, seller, engagement stats - none of which a
      search card carries. Costs one request per listing. Finds nothing new.

  [2] Look for new publications, walking up to 200 search page(s).
      One request returns ~25 listings, so this is the cheap way to grow the
      dataset. The new listings arrive as cards; details come on a later run.

  Which [1]:
```

It is skipped when `--deepen`/`--no-deepen` is passed explicitly, when nothing is card-only,
and when there is no terminal — so CI and unattended runs are unaffected.

Asking also avoids a trap. Left to decide for itself, a run deepens whenever *any* listing
lacks details. Listings sold and removed from Aruodas can never be fetched, and rows are
never deleted, so a handful of those would keep discovery from ever running again.

#### Skipping the wait entirely

Better still, don't let the run stall in the first place:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper scrape-live --solve-on-block
```

With `--solve-on-block` (or `solve_on_block: true` in the config), a block opens a browser
instead of starting a cooldown. Solve the challenge and the run **continues immediately** —
keeping its cache, its pacing, and everything already collected. No restart, no 25 minutes.

The learned burst ceiling is discarded on renewal, because it measured the cookie that was just
replaced. Carrying a ceiling of 6 into a session worth 100+ would put the run straight back into
the cooldowns the renewal just bought its way out of.

It needs a person at the keyboard, so it is **off by default**. Unattended and CI runs keep the
old wait-only behaviour, and a renewal that fails or is declined falls back to waiting rather
than ending the run. Because the run can now re-earn its session on demand, `--solve-on-block`
also lifts the refusal to start on a stale cookie.

Expect roughly five requests per 25-minute cooldown and four cooldowns per run. Since one search
page yields about 25 records, that is several hundred listings in a session. Note that the
learned ceiling only ever ratchets downward within a run; a fresh process re-learns it.

Why there is no cleverer approach available: see
[docs/data_surface_assessment.md](docs/data_surface_assessment.md).

### Corporate TLS interception

On networks with TLS-inspecting proxies (e.g. Zscaler), `httpx` cannot validate `aruodas.lt`'s
re-signed certificate and fails with `ConnectError while retrieving https://www.aruodas.lt/...`.
Point it at the corporate root CA:

```powershell
$env:SSL_CERT_FILE="C:\path\to\corp-ca.pem"
```

Use `setx SSL_CERT_FILE "C:\path\to\corp-ca.pem"` to persist this across new terminal sessions,
or set `ca_bundle` in `config/scrape.yaml`.

Live retrieval accepts only HTTPS Aruodas URLs, uses bounded retries, blocks off-domain
redirects, and rejects oversized responses. Use `--refresh-cache` to bypass local cached HTML.

Pacing before each uncached request is `min_delay_seconds` give or take `jitter_seconds` —
**3-7 seconds** as configured. About one request in eight also draws a 30-90 second *reading
pause*, because a person browsing listings does not request pages on a metronome and bounded
jitter alone cannot produce that shape. The mean gap is therefore nearer 12 seconds than 5.
Raising these values does not raise the yield: the ceiling is a per-IP request *count*, not a
rate, and the experiment behind that conclusion is recorded in `config/scrape.yaml`.

The `Scrape live listings` GitHub Actions workflow runs a fresh bounded snapshot every day at
04:17 UTC and can also be started manually with smaller limits. It uploads CSV/JSON diagnostics as
a workflow artifact retained for 30 days. It does not commit scraped data or preserve raw HTML.
See [docs/online_scraping.md](docs/online_scraping.md).

## Outputs

The offline pipeline generates:

```text
data/processed/apartments_vilnius.csv
data/processed/houses_vilnius.csv
data/processed/scrape_summary.json
data/processed/data_quality_report.json
data/processed/failed_urls.csv
data/processed/unknown_fields.csv
data/processed/run_history.csv        # online runs only, append-only
```

### Tracking growth across runs

`scrape_summary.json` is overwritten every run, so it only ever describes the last one.
`run_history.csv` is **append-only** — one row per online run, and nothing deletes rows:

| Column | Meaning |
|---|---|
| `total_known` | Publications in the export after this run — the running total |
| `listings_new` | Listings whose ID had never been seen before — what this walk actually added |
| `listings_discovered` | Cards seen this run, *including* ones already held — effort, not growth |
| `search_pages_fetched` | Search pages the **origin served** — requests actually spent |
| `detail_pages_fetched` | Detail pages the origin served |
| `pages_served_from_cache` | Pages read from disk. Cost no request, charged nothing |

`*_fetched` counts requests, never cache hits, so it can be compared directly against the
per-IP budget. A resumed run re-walks everything it already holds, so it will show a large
`pages_served_from_cache` and a small `search_pages_fetched` — that is the replay being free,
not the run doing less.

The distinction matters for scheduled re-runs. Walking the same pages ten days later re-sees
nearly every card, so `listings_discovered` stays high while the dataset barely grows.
**`listings_new` is the number that answers "what appeared since last time"**, and it falls
to zero when the site has nothing new for you.

Nothing is ever duplicated: records merge by `listing_id`, and a re-walk updates an existing
row rather than adding a second one.

CSV files have stable English headers. JSON-valued CSV columns are compact, deterministically
ordered JSON strings. See [docs/data_dictionary.md](docs/data_dictionary.md).

### Encoding

CSVs are written **UTF-8 with a BOM** so Excel on Windows opens them correctly on a
double-click. Without the BOM Excel assumes the legacy ANSI codepage and renders Lithuanian
as mojibake — `Visorių g.` as `VisoriÅ³ g.` — even though the file itself is perfectly valid.

Listing text stays in Lithuanian. The schema carries `title_lt`/`title_en` and
`description_lt`/`description_en`, but the `_en` columns are only populated when a
translation provider is configured, and none is by default, so they are empty.

JSON artifacts are plain UTF-8 with no BOM, which many parsers reject.

Anything reading these CSVs should use `utf-8-sig`, which handles files with and without the
mark. `pandas.read_csv(path, encoding="utf-8-sig")` is the usual line.

## Translation

`title_lt` and `description_lt` preserve the source text after whitespace cleanup. `title_en` and
`description_en` remain null unless a translation provider is explicitly implemented and enabled.
The default provider never summarizes, paraphrases, or invents text.

## Adding cities and fields

- Add cities and category URLs in [config/cities.yaml](config/cities.yaml); no core parser change is
	required. See [docs/adding_a_city.md](docs/adding_a_city.md).
- Add verified Lithuanian labels and categorical values in
	[config/field_mappings_lt_en.yaml](config/field_mappings_lt_en.yaml). Unknown labels are retained
	and reported. See [docs/field_mapping.md](docs/field_mapping.md).

## Development quality gates

```powershell
.venv\Scripts\python.exe -m black --check src tests
.venv\Scripts\python.exe -m isort --check-only src tests
.venv\Scripts\python.exe -m mypy src
.venv\Scripts\python.exe -m bandit -r src
.venv\Scripts\python.exe -m pytest --cov=aruodas_scraper --cov-report=term-missing --cov-fail-under=80
.venv\Scripts\python.exe -m build
```

Ruff is also configured. On systems where corporate application control blocks Ruff's bundled
native executable, it is intentionally not part of the local development requirements or
pre-commit hooks. Use Black, isort, mypy, and Bandit locally through `python -m ...`. Linux CI
installs and runs Ruff separately. Ruff has no supported pure-Python or Node/WASM command-line
implementation; WSL would be another native Linux route, but it is not installed on this machine.

## Documentation

- [Architecture](docs/architecture.md)
- [Data dictionary](docs/data_dictionary.md)
- [Is there a cheaper data surface?](docs/data_surface_assessment.md) — assessed, answer is no
- [Adding a city](docs/adding_a_city.md)
- [Field mapping](docs/field_mapping.md)
- [TDD evidence](docs/testing/aruodas-offline-pipeline.tdd.md)

## License

MIT. The license applies to this repository's code, not to third-party website content or data.
