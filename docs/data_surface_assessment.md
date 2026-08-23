# Is there a cheaper data surface than the rendered HTML?

Assessed 2026-08-20. **Answer: no.** This note exists so the question is not re-opened from
scratch; if you are about to go looking for a hidden JSON API, read this first.

## Why the question matters

Aruodas is behind PerimeterX, which enforces a per-source-IP request *count* budget. A run gets
roughly five requests before HTTP 403, then waits out a 20-25 minute block. Request count, not
bandwidth and not parsing effort, is the binding constraint on this project. Any surface that
returns more listings per request would beat the current approach outright.

## Method

Every candidate below was checked against the 24 pages already in `data/raw/cache/` (~6.4 MB,
2 search pages and 21 detail pages). **No live requests were spent on the table below**, which
matters: probing while inside a block renews its TTL. The two exceptions are `robots.txt` and
`sitemap.xml` at the end, which cannot be answered from cache and cost one request each.

## Findings

| Candidate | Evidence | Verdict |
|---|---|---|
| JSON-LD, detail pages | `<script type="application/ld+json">` is present, typed `["Product","SingleFamilyResidence"]`, but carries only `name`, `image`, `description`, `numberOfRooms`, `Offers.price`, `Offers.priceCurrency` and the agent's name/url/telephone. No area, build year, heating, condition, floor or coordinates. | Strictly **poorer** than `parsers/` already extracts |
| JSON-LD, search pages | `Organization` only — site name, logo, email. No listing data at all. | Useless |
| Hydration payload | No `__NEXT_DATA__`, no `window.__INITIAL_STATE__`, no large inline JSON. Only `window.dataLayer`, `window._pxAppId`, `window._adftrack`. The site is classic server-rendered HTML. | Absent |
| DoubleClick `DFPAudiencePixel` | Detail pages embed clean typed values in the pixel query string: `price`, `area`, `prperarea`, `aryear`, `rooms`, `WarmSystem`, `houseType`, `houseState`, `microdistrict`, `region`, `street`. Tempting — but search pages carry **two page-level pixels, not one per card**. | Saves **no** requests; only restates a page already fetched |
| AJAX endpoints in markup | `/ajax/checkUserLoggedIn/`, `/ajax/log_duration/`, `/ajax/reloadSavedSearches/`, `/send_message_contact_form/` — session, telemetry and contact-form endpoints. | No bulk listing API |
| Coordinates on search pages | Not present in markup or scripts. Detail pages expose them only inside a Google Maps `href`. | Detail fetch remains the only source |

Reproduce the pixel finding without a network request:

```bash
cd data/raw/cache
for f in *.html; do
  echo "$f cards=$(grep -c list-row-v2 "$f") pixels=$(grep -o 'DFPAudiencePixel[^"'"'"']*' "$f" | wc -l)"
done
```

Search pages report `cards=25 pixels=2`.

## Conclusion

The phase-A search-card walk is already the efficient surface: **one request yields ~25 records**,
each carrying price, price/m², rooms, area, floor, build year, heating, condition and address.
Nothing cheaper exists in the page.

A `sitemap.xml` would be *worse*, not better, and this is the counter-intuitive part. A sitemap
returns URLs carrying no data, so every listing found through one would still cost its own detail
request — one record per request instead of twenty-five. A sitemap is only worth having for
discovery completeness, if search pagination ever proves to miss listings.

### `robots.txt` and `sitemap.xml`, fetched 2026-08-20

Both were checked live, two requests total.

`robots.txt` returns **HTTP 200** and sits outside the protected path. It grants named crawlers
a narrow allow-list with `Crawl-delay: 10` (Googlebot, Bingbot, msnbot, `facebookexternalhit`,
`archive.org_bot`), blocks several by name outright (Yandexbot, Applebot, SeekportBot, LCC,
MegaIndex, SputnikBot), and closes with:

```
User-Agent: *
Disallow: /
```

**This scraper matches `*`.** This confirms rather than discovers: the directive is already
recorded in [connectivity_fix_plan.md](history/connectivity_fix_plan.md) under *Authorization*, where the
project owner's written authorization from Aruodas is what resolves it. The fetch above is the
first time the file was read directly, and the wording matches what that section describes.

It does sharpen one point. The allow-list request in that same plan is the route to being
*named* here alongside Googlebot and Bingbot, each of which gets an explicit stanza and a
`Crawl-delay: 10`. Being named is what would put the crawl inside the file's own terms rather
than relying on an agreement the bot-protection layer has no knowledge of.

`sitemap.xml` is advertised at the top of `robots.txt` but returns **HTTP 403** — and the body is
a **Cloudflare** block page, not a PerimeterX one. Since `robots.txt` returned 200 from the same
IP seconds earlier, this is not the per-IP budget; the sitemap path is separately protected. Note
also that Cloudflare sitting in front of PerimeterX means a 403 in this project has two possible
sources, which is worth remembering when diagnosing one.

So the sitemap is unavailable, which moots the yield question above — though the conclusion was
already that it would be worse, not better.

## What would actually raise yield

Not a different parsing surface. Only a change of source IP: the Aruodas allow-list request in
[connectivity_fix_plan.md](history/connectivity_fix_plan.md), for which written authorization already
exists, or running off a different network.
