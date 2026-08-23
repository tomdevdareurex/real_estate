# Connectivity Fix Plan: Corporate TLS/Proxy and Anti-Bot 403s

Status: planned
Date: 2026-08-18

## Summary

`python -m aruodas_scraper` fails to retrieve pages. The reported symptom was "aruodas.lt blocks us
with HTTP 407", but investigation found three distinct problems, none of which is a hard block by
the site.

## Diagnosis

### 1. Corporate TLS interception is unhandled

Zscaler (`ZSAService`, `ZSATunnel`, `ZSATrayManager` running) intercepts HTTPS, terminating TLS and
re-signing with a corporate CA. The workstation already trusts that CA three times over:

```
REQUESTS_CA_BUNDLE  -> C:\Users\wn686\corp-ca.pem
CURL_CA_BUNDLE      -> C:\Users\wn686\corp-ca.pem
NODE_EXTRA_CA_CERTS -> C:\Users\wn686\corp-ca.pem
```

**httpx reads none of these.** `networking/http_client.py:72-77` constructs `httpx.Client(...)`
without `verify=`, so it uses the bundled certifi roots, which do not contain the Zscaler CA.

Reproduced:

| Configuration | Result |
| --- | --- |
| httpx defaults (certifi) | `ConnectError: CERTIFICATE_VERIFY_FAILED` |
| `verify=corp-ca.pem` | `200`, 1.15 MB |
| `SSL_CERT_FILE=corp-ca.pem` | `200` (httpx honours this variable) |

The reported **407** is the same root cause on a different network path. In tunnel mode Zscaler
produces the certificate error above; as an explicit proxy it answers `407 Proxy Authentication
Required` expecting Kerberos/NTLM, which httpx cannot perform. A website cannot meaningfully issue
407 — it is by definition a proxy response.

### 2. The recorded failures are 403, not 407

Every row in `data/processed/failed_urls.csv` reads `HTTP 403`. `http_client.py:75` sends a single
header, `User-Agent: aruodas-scraper/0.1`, with no `Accept`, `Accept-Language`, or `Referer`.
Cloudflare and PerimeterX (`_pxAppId` is present in the page source) reject that on detail pages.

Reproduced: the URL `...mozuriskiu-g...-1-3669762/` from `failed_urls.csv`, fetched with a normal
browser header set, returned `200` with 243 KB and an intact listing container.

### 3. Error handling masks both problems

`http_client.py:137-140` collapses every 4xx into a generic `RetrievalError`, and neither 403 nor
407 appears in `_RETRYABLE_STATUSES` (`:18`). Failures are therefore immediate and carry no hint
about certificates, proxies, or headers — which is why the cause appeared to be the site.

## Authorization

Written authorization from Aruodas is held by the project owner, which resolves the
`User-agent: * / Disallow: /` directive in `robots.txt`. Request pacing in `rate_limiter.py:13-15`
(10s minimum plus 2-5s jitter) is unchanged by this work; nothing below increases request volume.

## Plan

### 1. TLS/CA resolution — `networking/tls.py` (new)

`resolve_ca_bundle(explicit: str | None)`, first match wins:

1. explicit `ca_bundle` from config or CLI
2. `SSL_CERT_FILE`
3. `REQUESTS_CA_BUNDLE`, then `CURL_CA_BUNDLE` (makes this workstation work with no new setup)
4. `truststore.SSLContext` (Windows trust store) when the package is importable
5. `True` (certifi default)

Log the winning source at INFO. Raise `ConfigurationError` when a configured path is missing.

### 2. Client wiring — `networking/http_client.py`

`FetchOptions` (`:25-50`) gains, each covered by `validate()`:

| Field | Default | Purpose |
| --- | --- | --- |
| `ca_bundle` | `None` | corporate CA path |
| `proxy` | `None` | explicit proxy URL; otherwise httpx `trust_env` reads `HTTPS_PROXY` |
| `http2` | `True` | Chrome negotiates h2; HTTP/1.1-only is an outlier |
| `user_agent` | realistic Chrome UA | overridable if Aruodas whitelists a specific string |

`httpx.Client(...)` receives `verify=resolve_ca_bundle(...)`, `proxy=`, `http2=`, and the full
header set. The client is already long-lived, so its cookie jar persists across requests — retain
that behaviour.

### 3. Browser-shaped headers — `networking/browser_profile.py` (new)

A single coherent header set. No user-agent rotation: under an authorization agreement a stable,
identifiable client is the correct choice.

```
User-Agent, Accept, Accept-Language: lt-LT,lt;q=0.9,en-US;q=0.8,en;q=0.7,
Accept-Encoding: gzip, deflate, br, Upgrade-Insecure-Requests,
Sec-Fetch-Dest/Mode/Site/User, sec-ch-ua*, Connection: keep-alive
```

`fetch()` gains `referer: str | None = None`; `pipelines/online.py` passes the originating search
URL when fetching a detail page. `Sec-Fetch-Site` is `same-origin` with a referer, `none` without.
The cache key stays URL-only (`cache.py`), unaffected by referer.

### 4. Error handling — `exceptions.py`, `http_client.py:128-156`

Add `ProxyAuthenticationError` and `BlockedError`, both subclassing `RetrievalError`:

- **407** -> `ProxyAuthenticationError`, not retried; the message names Zscaler and points at the
  `proxy` and `ca_bundle` settings. Fails the run promptly instead of exhausting every URL.
- **403** -> `BlockedError`, retried on its own longer backoff (~30s, 2 attempts) with cookies
  cleared between attempts so the session is re-established.
- `_RETRYABLE_STATUSES` (`:18`) keeps 429/5xx unchanged; 403 is handled in a separate branch so the
  two backoff profiles remain independent.

### 5. Diagnostics — `cli.py`

Add an `aruodas_scraper doctor` command reporting the resolved CA source and effective proxy, then
issuing one request each to `robots.txt` and a search page, printing the status with a targeted
hint per failure mode.

### 6. Configuration

- `run_config.py::ScrapeLiveOptions` (`:18-33`): add `user_agent: str | None`,
  `ca_bundle: Path | None`, `proxy: str | None`, `http2: bool | None`. `_resolve_paths` (`:96-102`)
  already resolves `Path` fields against the config file directory, which suits `ca_bundle`.
- `config/scrape.yaml`: add the keys, commented, documenting `ca_bundle` for Zscaler users.
- `cli.py` `scrape-live`: matching flags, preserving CLI > config file > default precedence.

### 7. Dependencies — `requirements.txt`, `pyproject.toml`

- `brotli` — **required**, because the header set advertises `Accept-Encoding: br`; without it
  responses decode to garbage. Currently absent.
- `h2` — HTTP/2 support. Currently absent.
- `truststore` — optional, enables the OS trust store on Windows.

### 8. Tests — `tests/unit/test_networking.py`

Extend the existing `httpx.MockTransport` approach (transport injection exists at `:62`):

- `resolve_ca_bundle` precedence order, including the missing-file error
- 407 raises `ProxyAuthenticationError` and is not retried
- 403 is retried with cookies cleared, then raises `BlockedError`
- the header set is present and `Sec-Fetch-Site` flips with the referer
- the cache key is unchanged by the referer

Coverage stays at or above 80%.

## Files touched

| Path | Change |
| --- | --- |
| `networking/tls.py` | new — CA resolution |
| `networking/browser_profile.py` | new — header set |
| `networking/http_client.py` | verify/proxy/http2, headers, referer, 403 and 407 branches |
| `exceptions.py` | `ProxyAuthenticationError`, `BlockedError` |
| `pipelines/online.py` | pass referer on detail fetches |
| `run_config.py`, `config/scrape.yaml`, `cli.py` | configuration plus `doctor` |
| `requirements.txt`, `pyproject.toml` | brotli, h2, truststore |
| `tests/unit/test_networking.py` | new cases |

Unchanged: `rate_limiter.py` pacing, `cache.py` keying, all parsers and normalization.

## Verification

1. `.venv\Scripts\python.exe -m pytest -q --cov=src` — suite green, coverage >= 80%.
2. `.venv\Scripts\python.exe -m aruodas_scraper doctor` — CA source reported as
   `REQUESTS_CA_BUNDLE`; robots.txt and search page both `200`.
3. Clear `data/raw/cache/`, then run `.venv\Scripts\python.exe -m aruodas_scraper` against the
   current `config/scrape.yaml` (vilnius, 1 page, 20 listings).
4. Confirm `data/processed/failed_urls.csv` holds only its header row and `scrape_summary.json`
   reports `failed: 0` with `apartments_exported` > 0.
5. Regression on the two known-bad inputs: the `/namai/vilniuje/` search page and the
   `...mozuriskiu-g...-1-3669762/` detail URL, both previously 403.
6. Negative check: pass `--ca-bundle` pointing at a nonexistent file and confirm the error is
   actionable rather than a bare stack trace.

## Follow-up (not code)

Request that Aruodas whitelist a specific User-Agent string or the source IP at their Cloudflare and
PerimeterX layer, then set that string via `user_agent` in `config/scrape.yaml`. Their bot
protection has no knowledge of the written authorization; whitelisting removes any reliance on
presenting browser-shaped headers and is the durable fix. Keep the authorization letter alongside
the code (for example `docs/authorization.md`) so the basis for automated access is documented.

## Addendum, 2026-08-18: the remaining 403s were a fingerprint mismatch

The header work above did not stop the 403s because it treated the symptom. The client claimed to be
Chrome while handshaking like Python, and PerimeterX scores the TLS and HTTP/2 fingerprints, which
httpx cannot change. Retrieval now runs through `curl_cffi` (`transport: curl`), which replays a real
Chrome fingerprint; pages that were 403 on httpx returned 200 with full listing HTML on every
attempt. See `docs/online_scraping.md` for the operational detail.

Driving real Chrome via Playwright was evaluated first and is not possible on a managed workstation:
Chrome's DevTools debugging port is disabled by policy (`RemoteDebuggingAllowed: 0`) and unsigned
binaries are refused by application control (`WinError 1260`). Selenium fails for both reasons too,
since it ships `chromedriver.exe` and drives Chrome over that same disabled port. `curl_cffi` avoids
both because it is an in-process library rather than an executable.

A matching fingerprint is necessary but not sufficient. The PerimeterX `_px3` cookie is minted by a
JS sensor no headless-free client runs, so unauthenticated retrieval has a small grace budget; once
spent, the origin blocks the source IP outright and fresh sessions do not help. Shared corporate
egress reaches that state fast, which is also the likeliest explanation for the original
half-succeeding pattern. The allow-list request above is therefore still the durable fix, not an
optional extra.
