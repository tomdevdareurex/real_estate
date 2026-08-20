# Architecture

```text
Local HTML -> classification -> scoped parser -> YAML mappings -> Pydantic validation
           -> category records -> checkpoints -> atomic CSV/JSON artifacts

City registry -> bounded HTTP retrieval -> search discovery -> detail retrieval
              -> scoped parser -> quality report -> atomic CSV/JSON artifacts
```

Offline and online pipelines share parsing, normalization, validation, and export layers. The
online pipeline keeps remote access behind `AruodasHttpClient`; automated tests use mocked HTTP
responses and synthetic HTML, never the production site.

Key boundaries:

- `cities.py`: strict city/category search URL and output configuration.
- `networking/`: host-restricted HTTP retrieval, exact-URL cache, request pacing, retries, redirect
    validation, and response-size limits.
- `discovery/`: genuine URL classification, deduplication, and pagination stop state.
- `parsers/`: listing-container-scoped extraction with structured-data fallbacks.
- `normalization/`: pure Lithuanian number, date, text, location, and unit normalization.
- `pipelines/`: resumable offline processing, bounded online orchestration, and atomic export.
- `validation/`: coordinate, record, and dataset-quality checks.
- `translation/`: optional provider protocol, disabled by default.
