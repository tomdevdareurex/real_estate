"""Transport seam separating how bytes are retrieved from the policy around retrieval.

Redirect following, pacing, retries, caching, size bounds, and URL validation all stay in
``AruodasHttpClient``. A fetcher only performs one GET and reports what came back, so a
transport can be swapped without moving any safety control.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from aruodas_scraper.exceptions import RetrievalError


class TransportError(RetrievalError):
    """A request failed below the HTTP status layer: connection, TLS, or timeout."""


@dataclass(frozen=True, slots=True)
class PageResponse:
    """A single HTTP response, with any redirect left for the caller to follow."""

    status_code: int
    headers: Mapping[str, str]
    body: bytes


class PageFetcher(Protocol):
    """Performs one HTTP GET, without following redirects."""

    def fetch_page(self, url: str, headers: Mapping[str, str]) -> PageResponse:
        """Return the response for ``url``, raising ``TransportError`` below the HTTP layer."""
        ...

    def clear_session(self) -> None:
        """Drop cookies so the next attempt negotiates a fresh session."""
        ...

    def set_cookie(self, cookie: str) -> None:
        """Replace the browser session cookie sent on every subsequent request.

        Exists so a run can adopt a freshly minted cookie without being restarted: the block
        that stops a run is cleared by re-earning the session, not by waiting, and a run that
        cannot swap its cookie has no way to use one.
        """
        ...

    def close(self) -> None:
        """Release the underlying connection pool."""
        ...


__all__ = ["PageFetcher", "PageResponse", "TransportError"]
