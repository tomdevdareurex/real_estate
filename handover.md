# Handover — Aruodas 403 fix (fingerprint mismatch)

Date: 2026-08-18. Branch `master`, nothing committed yet (whole tree is still untracked).

## What the problem actually was

The live scraper failed roughly half its detail-page fetches with HTTP 403. A previous session blamed
PerimeterX (`_pxAppId = "PXqLRSnBjb"`, behind Cloudflare) and added a full Chrome header set. Headers
alone cannot fix it.

**Root cause, proven empirically, not assumed:** the client *claimed* to be Chrome in its headers while
*handshaking* like Python. PerimeterX scores signals httpx structurally cannot fake — the TLS
fingerprint (JA3/JA4 cipher list, curve list, extension order) and the HTTP/2 fingerprint (SETTINGS
frame order, window size, pseudo-header order). A spike proved it: on 8 URLs that httpx returned 403
for, `curl_cffi` with `impersonate="chrome131"` returned 200 with full listing HTML — 8/8.

**Secondary finding:** a matching fingerprint is necessary but not sufficient. PerimeterX's `_px3`
cookie is minted by a JS sensor this client cannot run, so there is a finite grace budget per source
IP. Exhaust it and the origin blocks *everything* from that address — including a real browser — for
roughly **20–25 minutes**, then recovers. This was verified: a mid-session block cleared on its own,
and both cookie-bearing and cookie-free requests returned 200 afterwards. Do not mistake this
transient block for a permanent IP ban.

## Constraints that shaped the solution

- **This is a managed corporate workstation. External `.exe` files cannot be launched**
  (`OSError: [WinError 1260] blocked by group policy`). This killed Playwright *and* Selenium
  (chromedriver.exe, plus `RemoteDebuggingAllowed: 0` disables the CDP port anyway). It also blocks
  `ruff.exe` — lint by hand or not at all.
- `curl_cffi` was chosen precisely because it is an **in-process DLL binding**, no subprocess.
- Zscaler intercepts TLS; the corporate CA lives at `C:\Users\wn686\corp-ca.pem`
  (`REQUESTS_CA_BUNDLE`).
- Retrieval is covered by written authorization from Aruodas (`docs/connectivity_fix_plan.md:56-60`).
  Volume and pacing are unchanged by this work.

## What is already done (implementation complete)

### New files

| File | Purpose |
|---|---|
| `src/aruodas_scraper/networking/fetcher.py` | Transport seam: `TransportError`, `PageResponse`, `PageFetcher` Protocol |
| `src/aruodas_scraper/networking/curl_fetcher.py` | `CurlCffiFetcher`, `DEFAULT_IMPERSONATION = "chrome131"`, `curl_verify_option()` |
| `tests/unit/test_transport.py` | 27 tests |

`fetch_page` performs **one GET without following redirects**. Redirect looping, pacing, caching,
URL validation and the tenacity retry budgets all deliberately stay in `AruodasHttpClient`, so the
existing regression net still guards them.

### Modified files

- `networking/http_client.py` — `HttpxFetcher` extracted; `TransportName = Literal["curl","httpx"]`;
  `build_fetcher(options, trust, transport=None)` with a **lazy** curl import; `FetchOptions` gained
  `transport`, `impersonate`, `cookie`; `clear_session()` called on 403.
- `networking/browser_profile.py` — `_CHROME_MAJOR_VERSION` 139 → 151.
- `run_config.py` — `TransportOption`, plus `transport` / `impersonate` / `cookie_file` on
  `ScrapeLiveOptions`. Note `extra="forbid"`: every key in scrape.yaml needs a matching field.
- `cli.py` — `--transport` / `--impersonate` / `--cookie-file`; `_read_cookie_file()` with a 16 KiB
  bound; `doctor` rewritten to route its probes through `build_fetcher`, report the transport, and
  detect `px-captcha`.
- `config/scrape.yaml` — `transport: curl`, `impersonate: chrome131`, `http2: false`, Edge 151 UA,
  `cookie_file: ../../aruodas_secrets/cookie.txt`.
- `requirements.txt` / `pyproject.toml` — `curl_cffi==0.16.0`.
- `docs/online_scraping.md`, `docs/connectivity_fix_plan.md` — root cause and transport documented.

### Two traps that are easy to reintroduce

1. **`FetchOptions.transport` defaults to `"httpx"`, not `"curl"` — this is deliberate.** The test
   suite uses `respx`, which only intercepts httpx. A curl default makes 129 tests hit the real
   network. Every CLI path defaults to `curl` instead. The split is commented in the code; keep it.
2. **`_pxAppId` is NOT a block marker.** It appears on *successful* pages as the ordinary sensor
   script. Only `px-captcha` indicates a block. Using `_pxAppId` would false-positive every good
   response.

Also worth knowing: **newer impersonation profiles are not better.** `chrome136` is rejected where
`chrome124` and `chrome131` are accepted. If 403s reappear, try a different profile before anything
else.

### Test status

156 tests passing (129 pre-existing, unchanged + 27 new), coverage ~86.7%.

```
.venv/Scripts/python.exe -m pytest -q --cov=aruodas_scraper
```

## End-to-end verification: run completed, result below

`.venv/Scripts/python.exe -m aruodas_scraper scrape-live --refresh-cache` finished at 19:28 UTC /
21:28 local, exit code 0.

| | Baseline (httpx, pre-fix) | This run (curl, chrome131) |
|---|---|---|
| listings_discovered | 20 | 20 |
| detail_pages_fetched | 5 | 10 |
| apartments_exported | 5 | 10 |
| failed | 16 | 11 |

Twice the yield, but **the plan's success criterion of `failed: 0` was not met.**

### The failure pattern is the important part

Cache write timestamps show every successful fetch landed in one unbroken burst, 13 seconds apart:

```
21:15:41 21:15:54 21:16:07 21:16:21 21:16:34 21:16:49
21:17:02 21:17:15 21:17:28 21:17:42 21:17:56
```

That is 1 search page + 10 detail pages = **11 consecutive successes, zero failures**, over 2m15s.
Then nothing for the remaining ~10 minutes of the run: every one of the last 11 requests returned 403
with `px-captcha`.

**Read this correctly.** It is not a partial or flaky fix. A 100% success rate that terminates
abruptly at a request count is the signature of PerimeterX's per-IP grace budget for a client that
cannot run the JS sensor — exactly the secondary finding described at the top of this document. The
fingerprint work is validated: nothing failed while the budget lasted. The remaining ceiling is
account/IP reputation, not transport.

### Where to go next, most promising first

1. **Check whether the cookie was actually still valid.** `config/scrape.yaml` points at
   `../../aruodas_secrets/cookie.txt`, but that `_px3` was minted hours before this run. A live
   cookie should have raised the ceiling well beyond 11 requests; the fact that it did not suggests
   the cookie had expired, or that the UA in scrape.yaml no longer matches the browser that minted
   it (they must match — PX binds the session to the UA that earned it). Re-copy a fresh
   `Copy as cURL (bash)` from DevTools and rerun before concluding anything else.
2. **Test whether the budget is request-count or rate based.** Current pacing is ~13s. If a much
   slower run (say 45–60s) still stops at ~11 pages, the budget is per-count and pacing cannot fix
   it. This is a cheap, decisive experiment and it determines whether any client-side change remains
   worth trying.
3. **If both of the above fail, the remedy is the allow-list request**
   (`docs/connectivity_fix_plan.md:180-185`). There is already written authorization from Aruodas;
   this asks them to exempt the source IP from bot scoring. No client-side change can beat a JS
   sensor the client cannot execute.
4. Do not simply retry harder. The block has a ~20–25 min TTL and hammering it extends the problem.

Still unverified from the plan: spot-check two exported rows against the live pages. `selectolax`
targets server-rendered structure so this should hold, but verify rather than assume.
`doctor` should report `Transport: curl` and 200 for robots.txt and the search page.

## Cleanup and loose ends

- Throwaway probe files to delete: `%TEMP%\aruodas_cookie_probe.py`, `%TEMP%\aruodas_cookie.txt`.
  The working copy at `../aruodas_secrets/cookie.txt` (outside the repo) must stay.
- `.gitignore` has no cookie/secrets entry. The cookie lives outside the repo so nothing is currently
  at risk, but confirm with the user before the first commit.
- Nothing is committed yet. The entire change set is untracked.
- The GitHub Actions workflow still uses httpx. Runner IPs are scored far more harshly by PerimeterX,
  so expect it to fail; switching CI to curl was explicitly deferred ("local first, CI after").
