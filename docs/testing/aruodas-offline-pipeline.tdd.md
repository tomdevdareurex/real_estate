# Aruodas Offline Pipeline TDD Evidence

## Source

User requirements for a typed Aruodas listing parser and CSV export pipeline.

## Journeys

- A researcher can parse apartment and house HTML from local files.
- A researcher receives separate stable CSVs and complete diagnostic artifacts.
- A maintainer can update Lithuanian mappings without parser changes.

## RED and GREEN evidence

1. Configuration tests initially failed with `ModuleNotFoundError: aruodas_scraper`; after the
   settings and snapshot modules were added, the focused tests passed.
2. Normalization/discovery/parser tests initially failed because those packages were absent; after
   YAML-driven parsing was implemented, 14 focused tests passed.
3. Export/CLI tests initially failed because pipeline and CLI modules were absent; after atomic
   exports, checkpoints, validation, and Typer commands were implemented, 6 focused tests passed.

## Final verification

- Focused final resume regression: 1 passed.
- Full suite: 44 passed.
- Branch-aware coverage: 82.88%, above the required 80% threshold.
- Strict mypy: no issues in 51 source files.
- Bandit: no security issues.
- Black and isort checks: clean after formatting.
- Package build: source distribution and wheel built successfully.
- Installed-wheel smoke test: packaged field mappings loaded outside the repository root.
- CLI smoke test: help and configuration commands work.
- VS Code diagnostics: no Python errors.
- Ruff cannot run locally because its PyPI launcher executes an unsigned native `ruff.exe`, which
   corporate AppLocker blocks. No supported pure-Python or Node/WASM CLI exists, and WSL is not
   installed. Ruff is excluded from local dependencies and runs separately in Linux CI.
