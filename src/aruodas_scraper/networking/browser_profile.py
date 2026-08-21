"""Browser-consistent request headers for authorized Aruodas retrieval.

A single stable identity is used rather than a rotating pool: retrieval is covered by a
written authorization from Aruodas, so the client should stay recognisable to them.
"""

from __future__ import annotations

# Must stay in step with curl_fetcher.DEFAULT_IMPERSONATION. These headers declare an
# identity that the TLS/HTTP-2 handshake also declares, and the bot-protection layer
# cross-checks the two: a User-Agent and sec-ch-ua claiming a version the replayed
# fingerprint does not match is a stronger bot signal than an honest older version.
# Do not bump this ahead of a version that actually ships.
#
# These headers reach the wire only on the httpx transport: the curl transport imposes just
# Accept-Language and lets the impersonation profile supply User-Agent and sec-ch-ua, so that
# the header set cannot drift from the handshake. The pairing is kept anyway, so the two
# transports never claim different identities.
_CHROME_MAJOR_VERSION = "146"

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    f"(KHTML, like Gecko) Chrome/{_CHROME_MAJOR_VERSION}.0.0.0 Safari/537.36"
)

_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,image/apng,*/*;q=0.8"
)
_ACCEPT_LANGUAGE = "lt-LT,lt;q=0.9,en-US;q=0.8,en;q=0.7"


def default_headers(user_agent: str = DEFAULT_USER_AGENT) -> dict[str, str]:
    """Return the headers a browser sends on every top-level document request.

    Accept-Encoding is deliberately omitted: httpx advertises exactly the codings it can
    decode, so hardcoding one risks receiving a body the client cannot read.
    """
    return {
        "User-Agent": user_agent,
        "Accept": _ACCEPT,
        "Accept-Language": _ACCEPT_LANGUAGE,
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "sec-ch-ua": (
            f'"Chromium";v="{_CHROME_MAJOR_VERSION}", '
            f'"Google Chrome";v="{_CHROME_MAJOR_VERSION}", '
            '"Not;A=Brand";v="24"'
        ),
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
    }


def navigation_headers(referer: str | None) -> dict[str, str]:
    """Return per-request headers describing how this navigation was reached."""
    if referer is None:
        return {"Sec-Fetch-Site": "none"}
    return {"Referer": referer, "Sec-Fetch-Site": "same-origin"}


__all__ = ["DEFAULT_USER_AGENT", "default_headers", "navigation_headers"]
