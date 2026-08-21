# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

`AGENTS.md` holds a longer, continuously-updated log of architectural gotchas — read it before
changing networking, pacing, or pipeline code. `README.md` documents the CLI surface in full.

## Running commands on this machine

**Always invoke tools as modules through the venv interpreter**, rather than via generated
launchers (`pip.exe`, `ruff.exe`, `aruodas.exe`):

> The original reason — corporate application control refusing unsigned native executables with
> `WinError 1260` — no longer applies: as of 2026-08-21 `pip.exe --version` runs, user-mode code
> integrity is not enforced, and Smart App Control is off. The convention is kept because it is
> unambiguous and costs nothing, but it is no longer a hard constraint, and it must not be cited
> as a reason that Playwright/Selenium are impossible here. See AGENTS.md 2026-08-21.


```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m <tool> ...
```

The `PYTHONIOENCODING=utf-8` prefix is the established convention here — Lithuanian text in
output otherwise fails to encode on the Windows console.

Ruff is configured in `pyproject.toml` but is deliberately absent from `requirements-dev.txt` and
the pre-commit hooks for the same reason; only Linux CI runs it.

## Commands

```bash
# Setup
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pip install -e .

# Tests
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest tests/unit/test_budget.py -q
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q tests/integration/test_online_pipeline.py::test_a_later_run_deepens_a_listing_that_only_has_a_search_record
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest -q -k "deepening or re_seen_card"

# Full gate (mirrors .github/workflows/ci.yml)
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m black --check src tests
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m isort --check-only src tests
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m mypy src          # strict mode
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m bandit -r src
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m pytest --cov=aruodas_scraper --cov-report=term-missing --cov-fail-under=80
PYTHONIOENCODING=utf-8 .venv/Scripts/python.exe -m build
```

`make test` / `make check` wrap the same steps but default to bare `python`; pass
`PYTHON=.venv/Scripts/python.exe` if you use them.

Tests must be marked `@pytest.mark.unit` or `@pytest.mark.integration` (`--strict-markers` is on).

## Running the scraper

```bash
.venv/Scripts/python.exe -m aruodas_scraper doctor    # check transport, TLS, cookie age — run first
.venv/Scripts/python.exe -m aruodas_scraper           # runs whichever config section has enabled: true
.venv/Scripts/python.exe -m aruodas_scraper parse-offline --input data/raw --property-type all
```

Config precedence is **explicit CLI flag > `config/scrape.yaml` > command default**. `--no-config`
ignores the file entirely; CI workflows must pass it because `cookie_file` points at a path that
exists only on this workstation.

## Architecture

Both pipelines converge on the same parse → normalize → validate → export path:

```
offline:  local HTML → _classify by canonical URL → apartment/house parser ─┐
online:   cities.yaml → discovery → search cards + bounded detail fetches ──┴→
          parsers/common.py → YAML field mappings → Pydantic ListingRecord →
          quality report → atomic CSV/JSON in data/processed/
```

- **`cli.py`** — Typer app; every subcommand resolves options through `run_config.py`.
- **`config.py`** loads the *legacy* offline-only `config/default.yaml`; **`run_config.py`** loads
  `config/scrape.yaml`. They are different files with different schemas — don't conflate them.
- **`parsers/common.py`** does all real extraction. `apartment.py` and `house.py` are thin wrappers
  that add only derived fields (`apartment_total_area_sqm` vs `house_total_area_sqm`/`plot_area_*`).
- **`parsers/search_card.py`** extracts most attributes from a search-results card. One search page
  yields ~25 cards for one request, so card harvesting is the primary yield lever, not detail fetches.
- **`networking/budget.py`** — the origin's limit is a request **count** per source IP, not a rate.
  Slower pacing buys nothing (verified: 45 s spacing yielded 16 pages vs ~11 at 13 s). The module
  spends the budget in bursts separated by ~25-minute cooldowns and ratchets its learned ceiling
  downward within a run.
- **`normalization/translations.py`** validates `config/field_mappings_lt_en.yaml` against
  `ListingRecord.model_fields` at load time; unmapped labels are retained and reported, not dropped.

### Invariants worth preserving

- `ListingRecord` is frozen with `extra="forbid"` — adding a CSV column means adding a model field.
- All file writes are atomic (`.tmp` then `os.replace`); CSV export escapes formula injection.
- The HTTP client allows only HTTPS to `aruodas.lt` / `www.aruodas.lt`; anything else is `RetrievalError`.
- Online runs are **additive**: rows are merged by `listing_id` and never deleted. A card-only
  record still counts as owed a detail fetch, which is why repeat runs deepen the dataset.
- `curl_cffi` is the production transport (`chrome146` impersonation, which must agree with the
  browser that mints the cookie — see AGENTS.md 2026-08-21). `httpx` has a detectable
  fingerprint and is for tests/offline only. Keep `browser_profile._CHROME_MAJOR_VERSION` in step
  with `curl_fetcher.DEFAULT_IMPERSONATION`.
- Never read a default off a slotted frozen dataclass class object (e.g. `DelayPolicy.minimum_seconds`
  is a slot descriptor, not a value). Module constants like `DEFAULT_MINIMUM_DELAY_SECONDS` exist for
  that purpose. Encode symmetric pacing with `DelayPolicy.centred(center, jitter)`.
- Injected `sleeper` callables in `pipelines/online.py` keep tests from actually waiting.

## Data and secrets

- The PerimeterX `_px3` cookie lives **outside the repo** at `../aruodas_secrets/cookie.txt` and goes
  stale after roughly an hour. `doctor` reports its age. `.gitignore` blocks `aruodas_secrets/`,
  `*cookie*.txt`, and `*.pem`.
- Re-running while blocked renews the block's TTL. If a run reports the origin still refusing after
  4 cooldowns, wait ~30 minutes and refresh the cookie rather than retrying.
- Behind a TLS-intercepting proxy, set `SSL_CERT_FILE` or `ca_bundle` in `config/scrape.yaml`.
- Tests use synthetic HTML in `tests/fixtures/` only. Never commit production listing HTML, which
  carries personal contact details; `normalization/privacy.py` redacts them from descriptions.
- `data/raw`, `data/interim`, `data/processed`, and `logs` are gitignored.