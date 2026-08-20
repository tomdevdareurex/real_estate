"""Transport presenting a genuine Chrome TLS and HTTP/2 fingerprint via curl_cffi.

PerimeterX scores the TLS handshake (JA3/JA4), the HTTP/2 SETTINGS frame, and header
order. httpx cannot match Chrome on any of those, so a Chrome-shaped header set on a
Python handshake still earns an HTTP 403 - the client claims to be a browser while
handshaking like Python. curl_cffi replays a real Chrome fingerprint, which is what
actually unblocks retrieval. See docs/connectivity_fix_plan.md.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, cast

from curl_cffi import requests as curl_requests
from curl_cffi.requests import RequestsError

from aruodas_scraper.exceptions import RetrievalError
from aruodas_scraper.networking.fetcher import PageResponse, TransportError
from aruodas_scraper.networking.tls import TlsTrust

logger = logging.getLogger(__name__)

# Newer is not better: chrome136 is rejected by the origin where chrome124 and chrome131
# are accepted. Pinned to a profile verified against live search and detail pages, and
# worth re-checking if 403s reappear.
DEFAULT_IMPERSONATION = "chrome131"

# Only Accept-Language is imposed; everything else must come from the impersonation
# profile, because a header set that disagrees with the TLS fingerprint is the exact
# inconsistency this transport exists to remove.
_ACCEPT_LANGUAGE = "lt-LT,lt;q=0.9,en-US;q=0.8,en;q=0.7"


def curl_verify_option(trust: TlsTrust) -> str | bool:
    """Map resolved trust onto curl's verify argument, which needs a path, not a context."""
    if trust.bundle is not None:
        return str(trust.bundle)
    logger.warning(
        "The curl transport cannot use %s and will fall back to its bundled CA store. "
        "Behind a TLS-intercepting proxy, set 'ca_bundle' or REQUESTS_CA_BUNDLE.",
        trust.source,
    )
    return True


class CurlCffiFetcher:
    """Fetch pages over curl_cffi using a pinned Chrome impersonation profile."""

    def __init__(
        self,
        trust: TlsTrust,
        max_response_bytes: int,
        timeout_seconds: float = 30.0,
        impersonate: str = DEFAULT_IMPERSONATION,
        proxy: str | None = None,
        user_agent: str | None = None,
        cookie: str | None = None,
    ) -> None:
        """Open a curl_cffi session pinned to a Chrome fingerprint."""
        self._max_response_bytes = max_response_bytes
        self._extra_headers = {"Accept-Language": _ACCEPT_LANGUAGE}
        self._cookie = cookie
        if cookie is not None:
            # A session the bot-protection layer already trusts, minted by a real browser
            # on this address. Never logged: it authenticates this client to the origin.
            self._extra_headers["Cookie"] = cookie
            logger.info("Using a supplied browser session cookie (%d bytes).", len(cookie))
        if user_agent is not None:
            # Supported for the allow-list case, where Aruodas whitelist a specific
            # string, but it does weaken the match with the impersonated fingerprint.
            logger.warning(
                "Overriding the User-Agent supplied by impersonation profile %s.", impersonate
            )
            self._extra_headers["User-Agent"] = user_agent
        proxies = {"http": proxy, "https": proxy} if proxy else None
        # curl_cffi's annotations understate both arguments: `verify` is typed `bool` although
        # curl accepts a CA bundle path, which is the only way through a TLS-intercepting proxy,
        # and `proxies` is typed as a TypedDict that a plain scheme->URL mapping satisfies.
        self._session: curl_requests.Session[Any] = curl_requests.Session(
            impersonate=impersonate,
            verify=cast(bool, curl_verify_option(trust)),
            proxies=cast(Any, proxies),
            timeout=timeout_seconds,
            allow_redirects=False,
        )
        logger.info("Transport: curl_cffi impersonating %s", impersonate)

    def fetch_page(self, url: str, headers: Mapping[str, str]) -> PageResponse:
        """Return one response, enforcing the size bound while the body streams in."""
        merged = {**self._extra_headers, **headers}
        try:
            response = self._session.get(url, headers=merged, allow_redirects=False, stream=True)
        except RequestsError as error:
            raise TransportError(f"{type(error).__name__} while retrieving {url}") from error
        try:
            declared = response.headers.get("content-length")
            if declared is not None:
                try:
                    self._require_bounded_size(int(declared))
                except ValueError as error:
                    raise RetrievalError("Response has an invalid Content-Length header") from error
            chunks: list[bytes] = []
            read = 0
            try:
                for chunk in response.iter_content():
                    read += len(chunk)
                    self._require_bounded_size(read)
                    chunks.append(chunk)
            except RequestsError as error:
                raise TransportError(f"{type(error).__name__} while reading {url}") from error
            return PageResponse(
                status_code=response.status_code,
                headers=dict(response.headers),
                body=b"".join(chunks),
            )
        finally:
            response.close()

    def _require_bounded_size(self, size: int) -> None:
        if size > self._max_response_bytes:
            raise RetrievalError(f"Response exceeds {self._max_response_bytes} bytes")

    def clear_session(self) -> None:
        """Drop cookies so the next attempt negotiates a fresh session."""
        self._session.cookies.clear()

    def close(self) -> None:
        """Close the curl session."""
        self._session.close()


__all__ = ["DEFAULT_IMPERSONATION", "CurlCffiFetcher", "curl_verify_option"]
