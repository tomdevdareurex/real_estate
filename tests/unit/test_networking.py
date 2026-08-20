from pathlib import Path
from unittest.mock import Mock

import certifi
import httpx
import pytest
import respx

from aruodas_scraper.exceptions import (
    BlockedError,
    ConfigurationError,
    ProxyAuthenticationError,
    RetrievalError,
)
from aruodas_scraper.networking.browser_profile import DEFAULT_USER_AGENT
from aruodas_scraper.networking.cache import HtmlCache
from aruodas_scraper.networking.http_client import AruodasHttpClient, FetchOptions
from aruodas_scraper.networking.rate_limiter import DelayPolicy
from aruodas_scraper.networking.tls import resolve_tls_trust

_CA_ENVIRONMENT_VARIABLES = ("SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
# Real PEM material, because trust resolution parses the bundle it is handed.
_CERTIFICATE_PEM = Path(certifi.where()).read_bytes()


@pytest.fixture
def without_ca_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate certificate resolution from whatever the host machine exports."""
    for variable in _CA_ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(variable, raising=False)


class CountingStream(httpx.SyncByteStream):
    def __init__(self, chunks: tuple[bytes, ...]) -> None:
        self._chunks = chunks
        self.chunks_read = 0

    def __iter__(self):  # type: ignore[no-untyped-def]
        for chunk in self._chunks:
            self.chunks_read += 1
            yield chunk


@pytest.mark.unit
def test_html_cache_keeps_query_strings_as_distinct_keys(tmp_path: Path) -> None:
    cache = HtmlCache(tmp_path)

    cache.put("https://www.aruodas.lt/search?page=1", b"page one")

    assert cache.get("https://www.aruodas.lt/search?page=1") == b"page one"
    assert cache.get("https://www.aruodas.lt/search?page=2") is None


@pytest.mark.unit
@pytest.mark.parametrize(
    "policy",
    (
        DelayPolicy(minimum_seconds=-1),
        DelayPolicy(random_min_seconds=-1),
        DelayPolicy(random_min_seconds=3, random_max_seconds=2),
        DelayPolicy(pause_probability=1.5),
        DelayPolicy(pause_probability=-0.1),
        DelayPolicy(pause_min_seconds=-1),
        DelayPolicy(pause_min_seconds=90, pause_max_seconds=30),
    ),
)
def test_delay_policy_rejects_invalid_ranges(policy: DelayPolicy) -> None:
    with pytest.raises(ValueError):
        policy.validate()


@pytest.mark.unit
def test_delay_policy_never_pauses_by_default() -> None:
    policy = DelayPolicy.centred(5.0, 2.0)

    assert all(policy.wait(lambda _seconds: None) <= 7.0 for _ in range(200))


@pytest.mark.unit
def test_delay_policy_reading_pauses_break_up_the_delay_band() -> None:
    # Jitter is bounded by its own band, so it can never produce the occasional long gap a
    # reader does. The pause is what supplies that tail.
    policy = DelayPolicy.centred(5.0, 2.0, pause_probability=0.5)

    durations = [policy.wait(lambda _seconds: None) for _ in range(200)]

    assert all(3.0 <= duration <= 7.0 or 33.0 <= duration <= 97.0 for duration in durations)
    assert any(duration > 7.0 for duration in durations)
    assert any(duration <= 7.0 for duration in durations)


@pytest.mark.unit
def test_delay_policy_always_pauses_at_probability_one() -> None:
    policy = DelayPolicy.centred(5.0, 0.0, pause_probability=1.0)

    assert all(35.0 <= policy.wait(lambda _seconds: None) <= 95.0 for _ in range(50))


@pytest.mark.unit
def test_delay_policy_centred_stays_within_the_symmetric_band() -> None:
    # `wait` is additive over non-negative bounds, so "5 give or take 2" has to be encoded
    # as a floor of 3 plus a draw from [0, 4]. Assert the interval callers were promised.
    durations: list[float] = []
    policy = DelayPolicy.centred(5.0, 2.0)

    for _ in range(200):
        durations.append(policy.wait(lambda _seconds: None))

    assert policy.minimum_seconds == 3.0
    assert all(3.0 <= duration <= 7.0 for duration in durations)
    # A fixed delay would satisfy the bounds too; confirm the jitter is actually random.
    assert len(set(durations)) > 1


@pytest.mark.unit
def test_delay_policy_centred_passes_the_selected_duration_to_the_sleeper() -> None:
    sleeper = Mock()

    duration = DelayPolicy.centred(5.0, 2.0).wait(sleeper)

    sleeper.assert_called_once_with(duration)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("center", "jitter"),
    ((5.0, 6.0), (0.0, 0.1), (-1.0, 0.0), (5.0, -1.0)),
)
def test_delay_policy_centred_rejects_a_band_reaching_below_zero(
    center: float, jitter: float
) -> None:
    with pytest.raises(ValueError):
        DelayPolicy.centred(center, jitter)


@pytest.mark.unit
def test_delay_policy_centred_without_jitter_is_a_constant_delay() -> None:
    policy = DelayPolicy.centred(5.0, 0.0)

    assert policy.wait(lambda _seconds: None) == 5.0


@pytest.mark.unit
@respx.mock
def test_http_client_returns_cache_hit_without_request_or_delay(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai-vilniuje-1-1234567/"
    cache = HtmlCache(tmp_path)
    cache.put(url, b"cached")
    sleeper = Mock()

    with AruodasHttpClient(cache=cache, sleeper=sleeper) as client:
        content = client.fetch(url)

    assert content == b"cached"
    assert not respx.calls
    sleeper.assert_not_called()


@pytest.mark.unit
def test_http_client_rejects_oversized_cached_response_without_unbounded_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = "https://www.aruodas.lt/cached/"
    cache = HtmlCache(tmp_path)
    cache.put(url, b"123456")
    monkeypatch.setattr(
        Path,
        "read_bytes",
        lambda _path: (_ for _ in ()).throw(AssertionError("unbounded cache read")),
    )

    with AruodasHttpClient(
        cache=cache,
        options=FetchOptions(max_response_bytes=5),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="exceeds 5 bytes"):
            client.fetch(url)


@pytest.mark.unit
@respx.mock
def test_http_client_fetches_delays_and_caches_success(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai-vilniuje-1-1234567/"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"fresh"))
    cache = HtmlCache(tmp_path)
    sleeper = Mock()

    with AruodasHttpClient(
        cache=cache,
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=sleeper,
    ) as client:
        content = client.fetch(url)

    assert content == b"fresh"
    assert cache.get(url) == b"fresh"
    assert route.call_count == 1
    sleeper.assert_called_once_with(0.0)


@pytest.mark.unit
@respx.mock
def test_http_client_rejects_unsafe_url_before_request(tmp_path: Path) -> None:
    sleeper = Mock()

    with AruodasHttpClient(cache=HtmlCache(tmp_path), sleeper=sleeper) as client:
        with pytest.raises(RetrievalError, match="HTTPS Aruodas"):
            client.fetch("https://example.com/listing")

    assert not respx.calls
    sleeper.assert_not_called()


@pytest.mark.unit
@respx.mock
def test_http_client_retries_retryable_status_then_succeeds(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(
        side_effect=(httpx.Response(503), httpx.Response(200, content=b"ok"))
    )
    sleeper = Mock()

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=2, backoff_seconds=0),
        sleeper=sleeper,
    ) as client:
        content = client.fetch(url)

    assert content == b"ok"
    assert route.call_count == 2
    assert sleeper.call_count == 2


@pytest.mark.unit
@respx.mock
def test_http_client_retries_read_error_then_succeeds(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(
        side_effect=(httpx.ReadError("connection reset"), httpx.Response(200, content=b"ok"))
    )

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=2, backoff_seconds=0),
        sleeper=Mock(),
    ) as client:
        assert client.fetch(url) == b"ok"

    assert route.call_count == 2


@pytest.mark.unit
@respx.mock
def test_http_client_does_not_retry_ordinary_client_error(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/missing/"
    route = respx.get(url).mock(return_value=httpx.Response(404))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=3, backoff_seconds=0),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="HTTP 404"):
            client.fetch(url)

    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_client_blocks_off_domain_redirect(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/redirect/"
    route = respx.get(url).mock(
        return_value=httpx.Response(302, headers={"location": "https://example.com/trap"})
    )

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="redirect"):
            client.fetch(url)

    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_client_follows_bounded_same_host_redirect(tmp_path: Path) -> None:
    source = "https://www.aruodas.lt/redirect/"
    target = "https://www.aruodas.lt/target/"
    source_route = respx.get(source).mock(
        return_value=httpx.Response(302, headers={"location": "/target/"})
    )
    target_route = respx.get(target).mock(return_value=httpx.Response(200, content=b"ok"))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=Mock(),
    ) as client:
        content = client.fetch(source)

    assert content == b"ok"
    assert source_route.call_count == 1
    assert target_route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_client_rejects_oversized_response(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/large/"
    respx.get(url).mock(return_value=httpx.Response(200, content=b"123456"))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_response_bytes=5),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="exceeds 5 bytes"):
            client.fetch(url)


@pytest.mark.unit
@respx.mock
def test_http_client_stops_reading_oversized_stream(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/stream/"
    stream = CountingStream((b"123", b"456", b"789"))
    respx.get(url).mock(return_value=httpx.Response(200, stream=stream))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_response_bytes=5),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(RetrievalError, match="exceeds 5 bytes"):
            client.fetch(url)

    assert stream.chunks_read == 2


@pytest.mark.unit
def test_resolve_tls_trust_prefers_configured_bundle_over_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    configured = tmp_path / "configured.pem"
    configured.write_bytes(_CERTIFICATE_PEM)
    from_environment = tmp_path / "environment.pem"
    from_environment.write_bytes(_CERTIFICATE_PEM)
    monkeypatch.setenv("SSL_CERT_FILE", str(from_environment))

    trust = resolve_tls_trust(configured)

    assert trust.bundle == configured
    assert "configured ca_bundle" in trust.source


@pytest.mark.unit
def test_resolve_tls_trust_rejects_missing_configured_bundle(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        resolve_tls_trust(tmp_path / "absent.pem")


@pytest.mark.unit
def test_resolve_tls_trust_rejects_bundle_that_is_not_certificate_material(
    tmp_path: Path,
) -> None:
    bundle = tmp_path / "not-a-certificate.pem"
    bundle.write_text("this is not a certificate", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="could not be loaded"):
        resolve_tls_trust(bundle)


@pytest.mark.unit
@pytest.mark.parametrize("variable", _CA_ENVIRONMENT_VARIABLES)
def test_resolve_tls_trust_honours_each_certificate_environment_variable(
    variable: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_ca_environment: None,
) -> None:
    bundle = tmp_path / "corp-ca.pem"
    bundle.write_bytes(_CERTIFICATE_PEM)
    monkeypatch.setenv(variable, str(bundle))

    trust = resolve_tls_trust()

    assert trust.bundle == bundle
    assert variable in trust.source


@pytest.mark.unit
def test_resolve_tls_trust_skips_environment_variable_pointing_at_missing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    without_ca_environment: None,
) -> None:
    present = tmp_path / "present.pem"
    present.write_bytes(_CERTIFICATE_PEM)
    monkeypatch.setenv("SSL_CERT_FILE", str(tmp_path / "absent.pem"))
    monkeypatch.setenv("REQUESTS_CA_BUNDLE", str(present))

    trust = resolve_tls_trust()

    assert trust.bundle == present
    assert "REQUESTS_CA_BUNDLE" in trust.source


@pytest.mark.unit
@respx.mock
def test_http_client_sends_browser_headers_without_referer(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"ok"))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=Mock(),
    ) as client:
        client.fetch(url)

    headers = route.calls[0].request.headers
    assert headers["user-agent"] == DEFAULT_USER_AGENT
    assert headers["accept-language"] == "lt-LT,lt;q=0.9,en-US;q=0.8,en;q=0.7"
    assert headers["sec-fetch-mode"] == "navigate"
    assert headers["sec-fetch-site"] == "none"
    assert "referer" not in headers


@pytest.mark.unit
@respx.mock
def test_http_client_marks_referred_navigation_as_same_origin(tmp_path: Path) -> None:
    search_url = "https://www.aruodas.lt/butai/vilniuje/"
    detail_url = "https://www.aruodas.lt/butai-vilniuje-1-1234567/"
    route = respx.get(detail_url).mock(return_value=httpx.Response(200, content=b"ok"))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=Mock(),
    ) as client:
        client.fetch(detail_url, referer=search_url)

    headers = route.calls[0].request.headers
    assert headers["referer"] == search_url
    assert headers["sec-fetch-site"] == "same-origin"


@pytest.mark.unit
@respx.mock
def test_http_client_cache_key_ignores_referer(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai-vilniuje-1-1234567/"
    route = respx.get(url).mock(return_value=httpx.Response(200, content=b"ok"))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        sleeper=Mock(),
    ) as client:
        first = client.fetch(url, referer="https://www.aruodas.lt/butai/vilniuje/")
        second = client.fetch(url, referer="https://www.aruodas.lt/namai/vilniuje/")

    assert first == second == b"ok"
    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_client_fails_fast_on_proxy_authentication_required(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(return_value=httpx.Response(407))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(max_attempts=3, backoff_seconds=0),
        sleeper=Mock(),
    ) as client:
        with pytest.raises(ProxyAuthenticationError, match="HTTP 407"):
            client.fetch(url)

    assert route.call_count == 1


@pytest.mark.unit
@respx.mock
def test_http_client_retries_block_with_fresh_cookies_then_succeeds(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(
        side_effect=(
            httpx.Response(403, headers={"set-cookie": "_px3=blocked; Path=/"}),
            httpx.Response(200, content=b"ok"),
        )
    )
    retry_sleeper = Mock()

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(blocked_max_attempts=2, blocked_backoff_seconds=7.0),
        sleeper=Mock(),
        retry_sleeper=retry_sleeper,
    ) as client:
        assert client.fetch(url) == b"ok"

    assert route.call_count == 2
    assert "cookie" not in route.calls[1].request.headers
    retry_sleeper.assert_called_once_with(7.0)


@pytest.mark.unit
@respx.mock
def test_http_client_raises_blocked_error_after_exhausting_block_attempts(tmp_path: Path) -> None:
    url = "https://www.aruodas.lt/butai/vilniuje/"
    route = respx.get(url).mock(return_value=httpx.Response(403))

    with AruodasHttpClient(
        cache=HtmlCache(tmp_path),
        delay_policy=DelayPolicy(0, 0, 0),
        options=FetchOptions(
            max_attempts=5,
            backoff_seconds=0,
            blocked_max_attempts=2,
            blocked_backoff_seconds=0,
        ),
        sleeper=Mock(),
        retry_sleeper=Mock(),
    ) as client:
        with pytest.raises(BlockedError, match="allow-list"):
            client.fetch(url)

    assert route.call_count == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "options",
    (
        FetchOptions(blocked_max_attempts=0),
        FetchOptions(blocked_backoff_seconds=-1),
        FetchOptions(proxy="   "),
        FetchOptions(user_agent="  "),
    ),
)
def test_fetch_options_reject_invalid_connectivity_settings(options: FetchOptions) -> None:
    with pytest.raises(ValueError):
        options.validate()
