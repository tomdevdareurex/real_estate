# Online Scraping Operations

## Transport and bot protection

Aruodas run PerimeterX behind Cloudflare. PerimeterX scores the TLS handshake (JA3/JA4), the HTTP/2
SETTINGS frame, and header order, so a Chrome-shaped header set sent over a Python handshake is an
obvious mismatch and earns HTTP 403. Headers alone cannot close that gap.

`transport: curl` in `config/scrape.yaml` therefore routes retrieval through `curl_cffi`, which
replays a genuine Chrome fingerprint. Measured against pages that returned 403 on httpx, the curl
transport returned 200 with full listing HTML on every one. Keep `transport: httpx` only for offline
testing.

The `impersonate` profile is pinned to `chrome131`. Newer is not better: `chrome136` is rejected by
the origin where `chrome124` and `chrome131` are accepted. If 403s reappear, try another profile
before assuming anything else changed.

PerimeterX also serves challenges with **HTTP 200**. Those bodies carry `px-captcha` and are treated
as blocks so they are never cached or parsed as empty records. Note that `_pxAppId` appears on
ordinary successful pages too, as the sensor script, so it is not a block marker.

### When the source IP itself is blocked

A matching fingerprint does not buy unlimited requests. PerimeterX mints its `_px3` cookie from a JS
sensor this client never runs, so an unauthenticated client has only a small grace budget. Once that
is spent the origin serves `px-captcha` to every request from that IP, including brand-new sessions
with no cookies. Shared corporate egress addresses reach that state quickly.

`doctor` succeeding but a scrape failing minutes later is the signature of this state, not of a
configuration fault. The durable remedy is the allow-list request covered by the written
authorization; changing profiles or clearing cookies will not lift an IP-level block.

## Local smoke test

Run the smallest useful retrieval first:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper scrape-live `
  --city vilnius `
  --property-type apartments `
  --max-pages 1 `
  --max-listings-per-category 1
```

Inspect `scrape_summary.json`, `failed_urls.csv`, `data_quality_report.json`, and the generated CSV.
Validate the CSV with the existing `validate` command.

Each uncached HTTP attempt waits `min_delay_seconds` give or take `jitter_seconds`, drawn uniformly,
so the shipped `5.0` and `2.0` pace requests between 3 and 7 seconds apart. `jitter_seconds` may not
exceed `min_delay_seconds`, since the band would otherwise reach below zero.

Pacing is deliberately modest because the origin's tolerance is not a rate. A run paced at 45 seconds
still collected only 16 detail pages before 403s began, barely more than the ~11 seen at 13 seconds:
the limit is a per-IP request **count**, so waiting longer between requests buys almost nothing while
making every run an order of magnitude slower. Recovery of refused listings is handled by the retry
pass below instead.

Transport failures, HTTP 429, and selected 5xx responses receive at most three attempts with
exponential backoff. Ordinary 4xx responses are not retried. Requests are limited to HTTPS
`aruodas.lt` hosts, redirects are validated, and HTML responses cannot exceed 10 MiB.

## GitHub Actions

The `Scrape live listings` workflow supports manual dispatch and runs daily at 04:17 UTC. Scheduled
runs fetch both configured categories with at most two search pages and 40 detail pages per
category. Runs are serialized and have a 60-minute timeout.

Each run is a fresh snapshot. Its cache exists only for that job and is discarded afterward.
Successful CSV/JSON outputs and diagnostic reports are uploaded under an artifact named with the
workflow run ID and retained for 30 days. No raw HTML or generated data is committed.

Before relying on the schedule, manually dispatch one page and one listing. Confirm that the
GitHub-hosted runner can reach the source, request timing reflects the delay policy, both validation
steps pass where applicable, and the artifact contains no raw HTML.

## Deferred retry pass

An HTTP 403 means "not now", not "not available". Because the block is self-clearing, listings the
origin refuses are parked rather than discarded: after the whole traversal finishes, the run waits
`retry_cooldown_seconds` (default 300) and asks for each of them once more. Listings that were never
requested at all - because three consecutive blocks abandoned the main pass - are queued too, since
the same cooldown is what clears the block that stopped them.

A listing recovered this way is written to the CSV and removed from `failed_urls.csv`, so
`failed` counts only what was still failing when the run ended. `scrape_summary.json` reports
`deferred_retries_attempted` and `deferred_retries_recovered` so the pass can be judged.

There is exactly one pass. If the origin still refuses after a cooldown, the address is in the
sustained per-IP block described above, which no client-side wait resolves; further passes would
extend the run while renewing the block. Set `retry_cooldown_seconds: 0` to disable the pass and
leave 403s as plain failures.

Blocked **search** pages are not retried. Re-running discovery would renumber pagination and risk
duplicating or missing listings, so a refused search page stays recorded as a failure.

## Failures

- A failure on a category's first search page is fatal and produces a nonzero command exit.
- Later search-page and individual detail failures are written to `failed_urls.csv`; successfully
  parsed records are still exported.
- HTTP failures include status/type context but never response bodies.
- To stop scheduled access immediately, disable the workflow in the repository Actions settings or
  remove its `schedule` trigger.
