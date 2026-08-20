"""Transport seam: challenge detection, session clearing, and the curl_cffi fetcher."""

from collections.abc import Mapping
from pathlib import Path
from unittest.mock import Mock

import pytest
from curl_cffi.requests import RequestsError

from aruodas_scraper.exceptions import BlockedError, RetrievalError
from aruodas_scraper.networking import curl_fetcher as curl_module
from aruodas_scraper.networking.browser_profile import DEFAULT_USER_AGENT
from aruodas_scraper.networking.cache import HtmlCache
from aruodas_scraper.networking.curl_fetcher import CurlCffiFetcher, curl_verify_option
from aruodas_scraper.networking.fetcher import PageResponse, TransportError
from aruodas_scraper.networking.http_client import (
    AruodasHttpClient,
    FetchOptions,
    HttpxFetcher,
    build_fetcher,
)
from aruodas_scraper.networking.rate_limiter import DelayPolicy
from aruodas_scraper.networking.tls import TlsTrust

_URL = "https://www.aruodas.lt/butai/vilniuje/"

# A real page carries the sensor script, so _pxAppId alone must never read as a block.
_GOOD_PAGE = b"<html><script>window._pxAppId='PXqLRSnBjb'</script><div class='obj-details'>x</div>"
_CHALLENGE_PAGE = b"<html><body><div id='px-captcha'></div></body></html>"


class FakeFetcher:
    """Scripted PageFetcher recording how the client drove it."""

    def __init__(self, *responses: PageResponse) -> None:
        self._responses = list(responses)
        self.requests: list[str] = []
        self.sessions_cleared = 0
        self.closed = False

    def fetch_page(self, url: str, headers: Mapping[str, str]) -> PageResponse:
        self.requests.append(url)
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]

    def clear_session(self) -> None:
        self.sessions_cleared += 1

    def close(self) -> None:
        self.closed = True


def _client(cache_directory: Path, fetcher: FakeFetcher, **overrides: object) -> AruodasHttpClient:
    options = FetchOptions(
        max_attempts=1, backoff_seconds=0, blocked_backoff_seconds=0, **overrides
    )
    return AruodasHttpClient(
        cache=HtmlCache(cache_directory),
        delay_policy=DelayPolicy(0, 0, 0),
        options=options,
        sleeper=Mock(),
        retry_sleeper=Mock(),
        fetcher=fetcher,
    )


@pytest.mark.unit
def test_challenge_served_with_http_200_is_treated_as_a_block(tmp_path: Path) -> None:
    fetcher = FakeFetcher(PageResponse(status_code=200, headers={}, body=_CHALLENGE_PAGE))

    with _client(tmp_path, fetcher, blocked_max_attempts=1) as client:
        with pytest.raises(BlockedError, match="Bot-protection challenge served with HTTP 200"):
            client.fetch(_URL)


@pytest.mark.unit
def test_challenge_served_with_http_200_is_never_cached(tmp_path: Path) -> None:
    fetcher = FakeFetcher(PageResponse(status_code=200, headers={}, body=_CHALLENGE_PAGE))

    with _client(tmp_path, fetcher, blocked_max_attempts=1) as client:
        with pytest.raises(BlockedError):
            client.fetch(_URL)

    assert HtmlCache(tmp_path).get(_URL) is None


@pytest.mark.unit
def test_sensor_script_on_a_real_page_is_not_mistaken_for_a_challenge(tmp_path: Path) -> None:
    fetcher = FakeFetcher(PageResponse(status_code=200, headers={}, body=_GOOD_PAGE))

    with _client(tmp_path, fetcher) as client:
        assert client.fetch(_URL) == _GOOD_PAGE


@pytest.mark.unit
def test_block_clears_the_transport_session_before_retrying(tmp_path: Path) -> None:
    fetcher = FakeFetcher(
        PageResponse(status_code=403, headers={}, body=b""),
        PageResponse(status_code=200, headers={}, body=_GOOD_PAGE),
    )

    with _client(tmp_path, fetcher, blocked_max_attempts=2) as client:
        assert client.fetch(_URL) == _GOOD_PAGE

    assert fetcher.sessions_cleared == 1
    assert len(fetcher.requests) == 2


@pytest.mark.unit
def test_closing_the_client_closes_the_injected_transport(tmp_path: Path) -> None:
    fetcher = FakeFetcher(PageResponse(status_code=200, headers={}, body=_GOOD_PAGE))

    with _client(tmp_path, fetcher):
        pass

    assert fetcher.closed


class FakeCurlResponse:
    def __init__(
        self, status_code: int, headers: dict[str, str], chunks: tuple[bytes, ...]
    ) -> None:
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks
        self.closed = False

    def iter_content(self):  # type: ignore[no-untyped-def]
        yield from self._chunks

    def close(self) -> None:
        self.closed = True


class FakeCurlSession:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs
        self.sent_headers: dict[str, str] = {}
        self.response: object = FakeCurlResponse(200, {}, (b"ok",))
        self.error: Exception | None = None
        self.cookies = Mock()
        self.closed = False

    def get(self, url: str, headers: dict[str, str], **_kwargs: object) -> object:
        if self.error is not None:
            raise self.error
        self.sent_headers = headers
        return self.response

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_curl(monkeypatch: pytest.MonkeyPatch) -> list[FakeCurlSession]:
    """Replace the curl_cffi session so the fetcher can be exercised without a network."""
    created: list[FakeCurlSession] = []

    def factory(**kwargs: object) -> FakeCurlSession:
        session = FakeCurlSession(**kwargs)
        created.append(session)
        return session

    monkeypatch.setattr(curl_module.curl_requests, "Session", factory)
    return created


@pytest.mark.unit
def test_curl_fetcher_returns_status_headers_and_body(fake_curl: list[FakeCurlSession]) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)
    fake_curl[0].response = FakeCurlResponse(200, {"location": "/next"}, (b"ab", b"cd"))

    response = fetcher.fetch_page(_URL, {"Referer": "https://www.aruodas.lt/"})

    assert response.status_code == 200
    assert response.body == b"abcd"
    assert response.headers["location"] == "/next"


@pytest.mark.unit
def test_curl_fetcher_forwards_caller_headers_alongside_accept_language(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)

    fetcher.fetch_page(_URL, {"Referer": "https://www.aruodas.lt/"})

    assert fake_curl[0].sent_headers["Referer"] == "https://www.aruodas.lt/"
    assert fake_curl[0].sent_headers["Accept-Language"].startswith("lt-LT")
    # The impersonation profile owns the User-Agent unless one was chosen deliberately.
    assert "User-Agent" not in fake_curl[0].sent_headers


@pytest.mark.unit
def test_curl_fetcher_rejects_a_body_declared_over_the_size_bound(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=3)
    fake_curl[0].response = FakeCurlResponse(200, {"content-length": "9"}, (b"toolong",))

    with pytest.raises(RetrievalError, match="exceeds 3 bytes"):
        fetcher.fetch_page(_URL, {})


@pytest.mark.unit
def test_curl_fetcher_stops_reading_an_undeclared_body_at_the_size_bound(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=3)
    fake_curl[0].response = FakeCurlResponse(200, {}, (b"ab", b"cd"))

    with pytest.raises(RetrievalError, match="exceeds 3 bytes"):
        fetcher.fetch_page(_URL, {})


@pytest.mark.unit
def test_curl_fetcher_maps_a_transport_failure_onto_transport_error(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)
    fake_curl[0].error = RequestsError("connection reset")

    with pytest.raises(TransportError, match="while retrieving"):
        fetcher.fetch_page(_URL, {})


@pytest.mark.unit
def test_curl_fetcher_closes_the_response_even_when_the_size_bound_trips(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=3)
    oversized = FakeCurlResponse(200, {}, (b"abcd",))
    fake_curl[0].response = oversized

    with pytest.raises(RetrievalError):
        fetcher.fetch_page(_URL, {})

    assert oversized.closed


@pytest.mark.unit
def test_curl_verify_option_prefers_a_resolved_bundle_path(tmp_path: Path) -> None:
    bundle = tmp_path / "corp-ca.pem"

    assert curl_verify_option(TlsTrust(True, "explicit", bundle)) == str(bundle)


@pytest.mark.unit
def test_curl_verify_option_falls_back_when_trust_is_only_an_ssl_context() -> None:
    # curl needs a file path, so an in-memory context cannot be handed to it.
    assert curl_verify_option(TlsTrust(True, "system trust store", None)) is True


@pytest.mark.unit
def test_build_fetcher_selects_the_configured_transport(fake_curl: list[FakeCurlSession]) -> None:
    trust = TlsTrust(True, "test", None)

    curl = build_fetcher(FetchOptions(transport="curl"), trust)
    plain = build_fetcher(FetchOptions(transport="httpx"), trust)

    assert isinstance(curl, CurlCffiFetcher)
    assert isinstance(plain, HttpxFetcher)
    plain.close()


@pytest.mark.unit
def test_build_fetcher_forwards_only_a_deliberately_chosen_user_agent(
    fake_curl: list[FakeCurlSession],
) -> None:
    trust = TlsTrust(True, "test", None)

    build_fetcher(FetchOptions(transport="curl"), trust)
    build_fetcher(FetchOptions(transport="curl", user_agent="allow-listed/1.0"), trust)

    assert fake_curl[0].kwargs["impersonate"] == curl_module.DEFAULT_IMPERSONATION
    default_headers_sent = build_fetcher(FetchOptions(transport="curl"), trust)
    default_headers_sent.fetch_page(_URL, {})
    assert "User-Agent" not in fake_curl[2].sent_headers


@pytest.mark.unit
@pytest.mark.parametrize(
    "options",
    (
        FetchOptions(transport="playwright"),  # type: ignore[arg-type]
        FetchOptions(transport="curl", impersonate="  "),
    ),
)
def test_fetch_options_reject_an_unknown_transport(options: FetchOptions) -> None:
    with pytest.raises(ValueError):
        options.validate()


@pytest.mark.unit
def test_curl_fetcher_uses_the_pinned_impersonation_profile_by_default(
    fake_curl: list[FakeCurlSession],
) -> None:
    CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)

    assert fake_curl[0].kwargs["impersonate"] == "chrome131"
    assert fake_curl[0].kwargs["allow_redirects"] is False


@pytest.mark.unit
def test_curl_fetcher_clears_cookies_and_closes(fake_curl: list[FakeCurlSession]) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)

    fetcher.clear_session()
    fetcher.close()

    fake_curl[0].cookies.clear.assert_called_once_with()
    assert fake_curl[0].closed


@pytest.mark.unit
def test_supplied_cookie_is_sent_on_every_request(fake_curl: list[FakeCurlSession]) -> None:
    fetcher = CurlCffiFetcher(
        trust=TlsTrust(True, "test", None),
        max_response_bytes=1000,
        cookie="_px3=borrowed; PHPSESSID=abc",
    )

    fetcher.fetch_page(_URL, {"Referer": "https://www.aruodas.lt/"})

    assert fake_curl[0].sent_headers["Cookie"] == "_px3=borrowed; PHPSESSID=abc"


@pytest.mark.unit
def test_no_cookie_header_is_sent_when_none_was_supplied(
    fake_curl: list[FakeCurlSession],
) -> None:
    fetcher = CurlCffiFetcher(trust=TlsTrust(True, "test", None), max_response_bytes=1000)

    fetcher.fetch_page(_URL, {})

    assert "Cookie" not in fake_curl[0].sent_headers


@pytest.mark.unit
def test_build_fetcher_forwards_the_configured_cookie(fake_curl: list[FakeCurlSession]) -> None:
    fetcher = build_fetcher(
        FetchOptions(transport="curl", cookie="_px3=borrowed"), TlsTrust(True, "test", None)
    )

    fetcher.fetch_page(_URL, {})

    assert fake_curl[0].sent_headers["Cookie"] == "_px3=borrowed"


@pytest.mark.unit
def test_explicit_user_agent_reaches_the_curl_session(fake_curl: list[FakeCurlSession]) -> None:
    fetcher = CurlCffiFetcher(
        trust=TlsTrust(True, "test", None),
        max_response_bytes=1000,
        user_agent="allow-listed/1.0",
    )

    fetcher.fetch_page(_URL, {})

    assert fake_curl[0].sent_headers["User-Agent"] == "allow-listed/1.0"
    assert DEFAULT_USER_AGENT not in fake_curl[0].sent_headers.values()
