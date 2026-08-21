# Online Scraping Operations

## Transport and bot protection

Aruodas run PerimeterX behind Cloudflare. PerimeterX scores the TLS handshake (JA3/JA4), the HTTP/2
SETTINGS frame, and header order, so a Chrome-shaped header set sent over a Python handshake is an
obvious mismatch and earns HTTP 403. Headers alone cannot close that gap.

`transport: curl` in `config/scrape.yaml` therefore routes retrieval through `curl_cffi`, which
replays a genuine Chrome fingerprint. Measured against pages that returned 403 on httpx, the curl
transport returned 200 with full listing HTML on every one. Keep `transport: httpx` only for offline
testing.

The `impersonate` profile is pinned to `chrome146`, and the rule that governs the choice is that it
must **agree with the browser that mints `cookie_file`**. PerimeterX binds a session to the identity
that earned it, so a cookie copied out of Chrome 151 and replayed over a `chrome131` handshake is a
contradiction on every request — precisely the signal the curl transport exists to remove. Check
`chrome://version` in the browser you copy from and pick the nearest profile.

`curl_cffi` 0.16.0 ships `chrome131`, `chrome133a`, `chrome136`, `chrome142`, `chrome145` and
`chrome146`. If 403s reappear, step *down* that list before assuming anything else changed.

An earlier note here read "newer is not better: `chrome136` is rejected where `chrome124` and
`chrome131` are accepted". That was measured **cookie-free**, where there is no minting identity to
agree with and a profile is judged on its own; it does not carry over to a cookied run. It is
recorded so the experiment is not repeated blind, not as current guidance.

PerimeterX also serves challenges with **HTTP 200**. Those bodies carry `px-captcha` and are treated
as blocks so they are never cached or parsed as empty records. Note that `_pxAppId` appears on
ordinary successful pages too, as the sensor script, so it is not a block marker.

### The budget follows the cookie's trust, not the IP address

A matching fingerprint does not buy unlimited requests. PerimeterX mints its `_px3` cookie from a JS
sensor this client never runs, so what the origin will serve depends on how that cookie was earned.

**Measured 2026-08-22, and this is the single most important operational fact in the project:** a
cookie copied from an ordinary browse was spent after about **6** requests. After the challenge was
solved by hand in the browser, a cookie taken from that same session carried a run past **100**
requests with no refusal. The name `_px3` is therefore not a quality test — two cookies with that
name can differ by more than an order of magnitude in what they are worth. A solved challenge is
scored as evidence of a human, and the budget is raised to match.

So the response to a run dying at ~6 requests is **not** to wait, and not to change impersonation
profiles: it is to mint a cookie through a solved challenge, with `mint-cookie` below.

> An earlier version of this section claimed the block was **IP-level** — that a spent budget made
> the origin serve `px-captcha` to every request from that address "including brand-new sessions
> with no cookies", and that no client-side change could lift it. That is wrong, and it was an
> expensive thing to have written down: it argued against exactly the fix that works. It is recorded
> here so the conclusion is not reached again from the same evidence. Solving the challenge clears
> the state from the same address, immediately.

The allow-list request covered by the written authorization remains worth having, because it removes
the challenge step altogether. It is no longer the *only* remedy.

## Minting a cookie

`mint-cookie` replaces copying a Cookie header out of DevTools by hand:

```powershell
.venv\Scripts\python.exe -m aruodas_scraper mint-cookie
```

It opens the installed Chrome on a persistent profile, loads a search page, and waits. If a challenge
appears, solve it in the window; the command detects that the page has cleared, harvests the session
cookie, and writes it atomically to `cookie_file`. The value is never printed or logged.

Two properties are deliberate and should not be "optimised" away:

- **The browser is visible and is real Chrome** (`channel="chrome"`), not bundled Chromium and not
  headless. Headless Chrome puts `HeadlessChrome/151` in its own User-Agent and fails cheap sensor
  probes, so it draws a challenge it cannot then clear.
- **The solve is done by a person.** It is the human attestation the raised budget is paying for.
  Automating it would defeat the purpose and would fall outside the project's authorization.

The profile at `<cookie dir>/browser_profile/` remembers previous solves, so most mints need no
interaction at all — the command opens, sees a clean page, saves, and exits. It holds a live session
and lives beside the cookie file, outside the repository, for the same reason the cookie does.

### Renewing mid-run instead of waiting

`--solve-on-block` (or `solve_on_block: true`) changes what a block costs. Instead of starting a
cooldown, the run opens the browser at the moment the origin refuses it; you solve the challenge,
and the run continues **immediately**, keeping its cache, its pacing state and everything already
collected.

Two details make this work rather than merely shorten the wait:

- **The learned ceiling is discarded** (`RequestBudget.session_renewed`). It measured the cookie
  that was just replaced. Carrying an observation of 6 into a session worth 100+ would floor the
  new session at `minimum_burst` and send the run back into cooldowns it no longer needs.
- **The cookie is swapped on the live client** (`AruodasHttpClient.set_cookie`), not by rebuilding
  it, so the HTML cache and pacing survive the renewal.

Renewal is offered *before* the limits on `max_cooldowns` and `max_empty_bursts`, because those
bound how long a run may **wait**, and a renewal costs no waiting. It is bounded separately by
`max_session_renewals` (default 10), which exists only so a renewer that always reports success
cannot spin.

It needs a person at the keyboard and is therefore off by default; unattended and CI runs keep the
wait-only path unchanged. A renewal that fails or is declined falls back to the cooldown rather
than ending the run.

Playwright is an optional extra, since only this command needs it:

```powershell
.venv\Scripts\python.exe -m pip install playwright
```

No browser download is required; it drives the Chrome already on the machine.

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
