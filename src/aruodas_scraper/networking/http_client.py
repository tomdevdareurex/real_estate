"""Bounded HTTP retrieval for Aruodas pages."""

import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urljoin, urlsplit

import httpx
from tenacity import Retrying, retry_if_exception_type

from aruodas_scraper.constants import MAX_HTML_FILE_BYTES
from aruodas_scraper.exceptions import BlockedError, ProxyAuthenticationError, RetrievalError
from aruodas_scraper.networking.browser_profile import (
    DEFAULT_USER_AGENT,
    default_headers,
    navigation_headers,
)
from aruodas_scraper.networking.cache import CacheEntryTooLargeError, HtmlCache
from aruodas_scraper.networking.fetcher import PageFetcher, PageResponse, TransportError
from aruodas_scraper.networking.rate_limiter import DelayPolicy
from aruodas_scraper.networking.tls import TlsTrust, resolve_tls_trust

logger = logging.getLogger(__name__)

TransportName = Literal["curl", "httpx"]
_TRANSPORT_NAMES = frozenset({"curl", "httpx"})

_ALLOWED_HOSTS = frozenset({"aruodas.lt", "www.aruodas.lt"})
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})

# The bot-protection layer also serves challenges with HTTP 200, so status alone is not
# enough. Only px-captcha discriminates: _pxAppId appears on ordinary pages too, as the
# sensor script, so matching on it would reject every good response.
_CHALLENGE_MARKERS = (b"px-captcha",)

_PROXY_AUTH_HINT = (
    "An intercepting proxy requires authentication (HTTP 407). This is a corporate network "
    "issue, not an Aruodas block. Set 'proxy' in config/scrape.yaml to a proxy URL that "
    "includes credentials, or run off the corporate VPN."
)
_BLOCKED_HINT = (
    "The origin rejected the request as automated traffic. The usual cause is a transport "
    "whose TLS/HTTP2 fingerprint does not match the browser it claims to be: set "
    "'transport: curl' in config/scrape.yaml. If it is already set, try another "
    "'impersonate' profile, or ask Aruodas to allow-list the source IP."
)


class _RetryableRetrievalError(RetrievalError):
    """Internal signal for failures eligible for a bounded retry."""


class _RetryableBlockedError(BlockedError):
    """Internal signal for a block eligible for a bounded, slower retry."""


@dataclass(frozen=True, slots=True)
class FetchOptions:
    """Safety and retry limits for remote retrieval."""

    timeout_seconds: float = 30.0
    max_attempts: int = 3
    backoff_seconds: float = 1.0
    max_redirects: int = 3
    max_response_bytes: int = MAX_HTML_FILE_BYTES
    user_agent: str = DEFAULT_USER_AGENT
    ca_bundle: Path | None = None
    proxy: str | None = None
    http2: bool = True
    # Defaults to the dependency-light transport so library callers and tests stay on
    # plain httpx. Every CLI path defaults to curl instead, because that is the one that
    # actually talks to a fingerprint-scoring origin.
    transport: TransportName = "httpx"
    impersonate: str | None = None
    cookie: str | None = None
    blocked_max_attempts: int = 2
    blocked_backoff_seconds: float = 30.0

    def validate(self) -> None:
        """Raise when any retrieval bound is invalid."""
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive.")
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one.")
        if self.backoff_seconds < 0:
            raise ValueError("backoff_seconds cannot be negative.")
        if self.max_redirects < 0:
            raise ValueError("max_redirects cannot be negative.")
        if self.max_response_bytes < 1:
            raise ValueError("max_response_bytes must be positive.")
        if not self.user_agent.strip():
            raise ValueError("user_agent cannot be empty.")
        if self.blocked_max_attempts < 1:
            raise ValueError("blocked_max_attempts must be at least one.")
        if self.blocked_backoff_seconds < 0:
            raise ValueError("blocked_backoff_seconds cannot be negative.")
        if self.proxy is not None and not self.proxy.strip():
            raise ValueError("proxy cannot be empty when set.")
        if self.transport not in _TRANSPORT_NAMES:
            raise ValueError("transport must be curl or httpx.")
        if self.impersonate is not None and not self.impersonate.strip():
            raise ValueError("impersonate cannot be empty when set.")


class HttpxFetcher:
    """Fetch pages over httpx.

    Retained for tests and offline use. Against a fingerprint-scoring origin this
    transport is detectable no matter which headers it sends; prefer ``CurlCffiFetcher``.
    """

    def __init__(
        self,
        trust: TlsTrust,
        max_response_bytes: int,
        timeout_seconds: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        proxy: str | None = None,
        http2: bool = True,
        transport: httpx.BaseTransport | None = None,
        cookie: str | None = None,
    ) -> None:
        """Open an httpx client with browser-shaped headers."""
        self._max_response_bytes = max_response_bytes
        headers = default_headers(user_agent)
        if cookie is not None:
            headers = {**headers, "Cookie": cookie}
        self._client = httpx.Client(
            timeout=timeout_seconds,
            follow_redirects=False,
            headers=headers,
            verify=trust.verify,
            proxy=proxy,
            http2=http2 and _http2_supported(),
            transport=transport,
        )

    def fetch_page(self, url: str, headers: Mapping[str, str]) -> PageResponse:
        """Return one response, enforcing the size bound while the body streams in."""
        try:
            with self._client.stream("GET", url, headers=dict(headers)) as response:
                return PageResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    body=self._read_response(response),
                )
        except httpx.TransportError as error:
            raise TransportError(f"{type(error).__name__} while retrieving {url}") from error

    def _read_response(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("content-length")
        if content_length is not None:
            try:
                declared_length = int(content_length)
            except ValueError as error:
                raise RetrievalError("Response has an invalid Content-Length header") from error
            self._require_bounded_size(declared_length)

        chunks: list[bytes] = []
        bytes_read = 0
        for chunk in response.iter_bytes():
            bytes_read += len(chunk)
            self._require_bounded_size(bytes_read)
            chunks.append(chunk)
        return b"".join(chunks)

    def _require_bounded_size(self, size: int) -> None:
        if size > self._max_response_bytes:
            raise RetrievalError(f"Response exceeds {self._max_response_bytes} bytes")

    def clear_session(self) -> None:
        """Drop cookies so the next attempt negotiates a fresh session."""
        self._client.cookies.clear()

    def set_cookie(self, cookie: str) -> None:
        """Adopt a newly minted browser session for every subsequent request."""
        self._client.headers["Cookie"] = cookie
        # Whatever httpx accumulated belongs to the session being replaced; sending both
        # would put two generations of the same token on one request.
        self._client.cookies.clear()

    def close(self) -> None:
        """Close the underlying connection pool."""
        self._client.close()


def build_fetcher(
    options: FetchOptions,
    trust: TlsTrust,
    transport: httpx.BaseTransport | None = None,
) -> PageFetcher:
    """Construct the transport named by ``options``, importing curl_cffi only when asked."""
    if options.transport == "httpx":
        return HttpxFetcher(
            trust=trust,
            max_response_bytes=options.max_response_bytes,
            timeout_seconds=options.timeout_seconds,
            user_agent=options.user_agent,
            proxy=options.proxy,
            http2=options.http2,
            transport=transport,
            cookie=options.cookie,
        )
    from aruodas_scraper.networking.curl_fetcher import DEFAULT_IMPERSONATION, CurlCffiFetcher

    return CurlCffiFetcher(
        trust=trust,
        max_response_bytes=options.max_response_bytes,
        timeout_seconds=options.timeout_seconds,
        impersonate=options.impersonate or DEFAULT_IMPERSONATION,
        proxy=options.proxy,
        # The impersonation profile carries its own User-Agent, and one that disagrees with
        # the TLS fingerprint is the inconsistency this transport exists to remove. Forward
        # only a deliberately chosen string, such as one Aruodas have allow-listed.
        user_agent=options.user_agent if options.user_agent != DEFAULT_USER_AGENT else None,
        cookie=options.cookie,
    )


class AruodasHttpClient:
    """Fetch Aruodas HTML with caching, delays, retries, and strict limits."""

    def __init__(
        self,
        cache: HtmlCache,
        delay_policy: DelayPolicy = DelayPolicy(),
        options: FetchOptions = FetchOptions(),
        sleeper: Callable[[float], None] = time.sleep,
        retry_sleeper: Callable[[float], None] = time.sleep,
        transport: httpx.BaseTransport | None = None,
        fetcher: PageFetcher | None = None,
    ) -> None:
        """Initialize a retrieval client with injectable timing and transport."""
        delay_policy.validate()
        options.validate()
        self._cache = cache
        self._delay_policy = delay_policy
        self._options = options
        self._sleeper = sleeper
        self._retry_sleeper = retry_sleeper
        if fetcher is None:
            trust = resolve_tls_trust(options.ca_bundle)
            logger.info("TLS trust resolved from %s", trust.source)
            fetcher = build_fetcher(options, trust, transport)
        self._fetcher = fetcher

    def __enter__(self) -> "AruodasHttpClient":
        """Return the open client."""
        return self

    def __exit__(self, *_args: object) -> None:
        """Close the underlying connection pool."""
        self.close()

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._fetcher.close()

    def set_cookie(self, cookie: str) -> None:
        """Adopt a newly minted browser session mid-run, without rebuilding the client.

        Rebuilding instead would drop the cache and the pacing state along with the block.
        """
        self._fetcher.set_cookie(cookie)

    def has_cached(self, url: str) -> bool:
        """Whether ``fetch`` would answer from disk without contacting the origin.

        Exists so a caller can tell the two cases apart *before* fetching. A cached page
        costs no request, so charging it against a per-IP request budget - or counting it
        as a page fetched - overstates both. That matters most on a resumed run, which
        re-walks every page it already holds.
        """
        return self._cache.has(url)

    def fetch(self, url: str, refresh: bool = False, referer: str | None = None) -> bytes:
        """Return one safe HTML response, using the exact URL as its cache key.

        Args:
            url: Absolute HTTPS Aruodas URL to retrieve.
            refresh: Bypass any cached copy and re-request the page.
            referer: Page this navigation was reached from, mirroring browser behaviour.
                It never participates in the cache key.

        Returns:
            Response body bytes, bounded by ``max_response_bytes``.

        Raises:
            ProxyAuthenticationError: If an intercepting proxy demands credentials.
            BlockedError: If the origin rejects the request as automated traffic.
            RetrievalError: If the page cannot be retrieved within the configured bounds.
        """
        self._validate_url(url)
        if not refresh:
            try:
                cached = self._cache.get(url, max_bytes=self._options.max_response_bytes)
            except CacheEntryTooLargeError as error:
                raise RetrievalError(
                    f"Cached response exceeds {self._options.max_response_bytes} bytes"
                ) from error
            if cached is not None:
                return cached

        retrying = Retrying(
            sleep=self._retry_sleeper,
            stop=self._stop,
            wait=self._wait,
            retry=retry_if_exception_type((_RetryableRetrievalError, _RetryableBlockedError)),
            reraise=True,
        )
        try:
            for attempt in retrying:
                with attempt:
                    content = self._fetch_with_redirects(url, referer)
                    self._cache.put(url, content)
                    return content
        except _RetryableBlockedError as error:
            # Surface the public type once retries are exhausted; the private class exists only
            # to steer tenacity between two independent backoff profiles.
            raise BlockedError(str(error)) from error
        except _RetryableRetrievalError as error:
            raise RetrievalError(str(error)) from error
        raise RetrievalError(f"Failed to retrieve {url}")

    def _stop(self, state: object) -> bool:
        """Stop after the attempt budget belonging to whichever failure mode is in play."""
        attempt_number = int(getattr(state, "attempt_number", 1))
        if _is_blocked(state):
            return attempt_number >= self._options.blocked_max_attempts
        return attempt_number >= self._options.max_attempts

    def _wait(self, state: object) -> float:
        """Back off gently for transient errors and far more slowly for blocks."""
        if _is_blocked(state):
            return self._options.blocked_backoff_seconds
        attempt_number = int(getattr(state, "attempt_number", 1))
        return float(self._options.backoff_seconds * (2 ** max(attempt_number - 1, 0)))

    def _fetch_with_redirects(self, url: str, referer: str | None) -> bytes:
        try:
            return self._fetch_redirect_chain(url, referer)
        except (_RetryableRetrievalError, _RetryableBlockedError):
            raise
        except TransportError as error:
            raise _RetryableRetrievalError(str(error)) from error

    def _fetch_redirect_chain(self, url: str, referer: str | None) -> bytes:
        current_url = url
        headers = navigation_headers(referer)
        for redirect_count in range(self._options.max_redirects + 1):
            self._delay_policy.wait(self._sleeper)
            response = self._fetcher.fetch_page(current_url, headers)
            if response.status_code == 407:
                raise ProxyAuthenticationError(
                    f"HTTP 407 while retrieving {current_url}. {_PROXY_AUTH_HINT}"
                )
            if response.status_code == 403:
                raise self._blocked(current_url, f"HTTP 403 while retrieving {current_url}.")
            if response.status_code in _RETRYABLE_STATUSES:
                raise _RetryableRetrievalError(
                    f"HTTP {response.status_code} while retrieving {current_url}"
                )
            if response.status_code >= 400:
                raise RetrievalError(f"HTTP {response.status_code} while retrieving {current_url}")
            if response.status_code not in _REDIRECT_STATUSES:
                if _is_challenge(response.body):
                    raise self._blocked(
                        current_url,
                        f"Bot-protection challenge served with HTTP {response.status_code} "
                        f"while retrieving {current_url}.",
                    )
                return response.body
            if redirect_count == self._options.max_redirects:
                raise RetrievalError(f"Too many redirects while retrieving {url}")
            location = response.headers.get("location")
            if not location:
                raise RetrievalError(f"HTTP redirect from {current_url} has no location")
            next_url = urljoin(current_url, location)
            try:
                self._validate_url(next_url)
            except RetrievalError as error:
                raise RetrievalError(f"Unsafe redirect from {current_url}: {location}") from error
            current_url = next_url
        raise RetrievalError(f"Too many redirects while retrieving {url}")

    def _blocked(self, url: str, summary: str) -> "_RetryableBlockedError":
        """Drop the session so the next attempt negotiates fresh cookies, then signal a block."""
        self._fetcher.clear_session()
        return _RetryableBlockedError(f"{summary} {_BLOCKED_HINT}")

    @staticmethod
    def _validate_url(url: str) -> None:
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError as error:
            raise RetrievalError("URL must be a valid HTTPS Aruodas URL") from error
        if (
            parts.scheme != "https"
            or parts.hostname not in _ALLOWED_HOSTS
            or parts.username is not None
            or parts.password is not None
            or port not in {None, 443}
            or not parts.path.startswith("/")
        ):
            raise RetrievalError("URL must be an HTTPS Aruodas URL")


def _is_challenge(body: bytes) -> bool:
    """Return whether a 2xx body is really a bot-protection interstitial."""
    return any(marker in body for marker in _CHALLENGE_MARKERS)


def _is_blocked(state: object) -> bool:
    """Return whether the most recent attempt failed because the origin blocked it."""
    outcome = getattr(state, "outcome", None)
    if outcome is None or not outcome.failed:
        return False
    return isinstance(outcome.exception(), _RetryableBlockedError)


def _http2_supported() -> bool:
    """Return whether the optional h2 dependency is installed."""
    try:
        import h2  # noqa: F401
    except ImportError:
        logger.warning("HTTP/2 requested but the 'h2' package is missing; using HTTP/1.1.")
        return False
    return True


__all__ = ["AruodasHttpClient", "FetchOptions", "HttpxFetcher", "TransportName", "build_fetcher"]
