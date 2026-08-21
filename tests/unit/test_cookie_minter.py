"""Unit tests for browser-minted bot-protection cookies."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.networking import cookie_minter
from aruodas_scraper.networking.cookie_minter import mint_cookie
from aruodas_scraper.networking.cookie_source import load_cookie_file, write_cookie_file

_CHALLENGE_PAGE = "<html><body><div id='px-captcha'></div></body></html>"
_REAL_PAGE = "<html><body><div class='list-row'>Butas</div></body></html>"


class _FakeClock:
    """Monotonic time the test advances itself, so no test ever really waits."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakePage:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.visited: list[str] = []

    def goto(self, url: str, **_: Any) -> None:
        self.visited.append(url)

    def content(self) -> str:
        # Hold on the last body once the script is exhausted, so a loop that keeps polling
        # sees a stable page instead of running off the end of the list.
        return self._contents.pop(0) if len(self._contents) > 1 else self._contents[0]


class _FakeContext:
    def __init__(self, page: _FakePage, cookies: list[dict[str, Any]]) -> None:
        self.pages: list[_FakePage] = []
        self._page = page
        self._cookies = cookies
        self.closed = False

    def new_page(self) -> _FakePage:
        return self._page

    def cookies(self) -> list[dict[str, Any]]:
        return list(self._cookies)

    def close(self) -> None:
        self.closed = True


class _FakeDriver:
    def __init__(self, context: _FakeContext) -> None:
        self.chromium = self
        self._context = context
        self.launch_kwargs: dict[str, Any] = {}

    def launch_persistent_context(self, **kwargs: Any) -> _FakeContext:
        self.launch_kwargs = kwargs
        return self._context


def _install_fake(
    monkeypatch: pytest.MonkeyPatch,
    *,
    contents: list[str],
    cookies: list[dict[str, Any]],
) -> tuple[_FakeDriver, _FakeContext, _FakePage]:
    page = _FakePage(contents)
    context = _FakeContext(page, cookies)
    driver = _FakeDriver(context)

    @contextmanager
    def fake_playwright() -> Iterator[_FakeDriver]:
        yield driver

    monkeypatch.setattr(cookie_minter, "_load_playwright", lambda: fake_playwright)
    return driver, context, page


def _px_cookie(value: str = "abc123") -> dict[str, Any]:
    return {"name": "_px3", "value": value, "domain": ".aruodas.lt"}


@pytest.mark.unit
def test_an_untroubled_page_mints_a_cookie_without_reporting_a_solve(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[_px_cookie()])
    clock = _FakeClock()

    minted = mint_cookie(
        profile_dir=tmp_path / "profile", sleeper=clock.advance, clock=clock, headless=True
    )

    assert minted.header == "_px3=abc123"
    assert minted.solved_challenge is False


@pytest.mark.unit
def test_a_challenge_is_announced_once_and_waited_out(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The prompt must not repeat every poll, or it buries the window it is pointing at."""
    _install_fake(
        monkeypatch,
        contents=[_CHALLENGE_PAGE, _CHALLENGE_PAGE, _CHALLENGE_PAGE, _REAL_PAGE],
        cookies=[_px_cookie()],
    )
    clock = _FakeClock()
    announcements: list[int] = []

    minted = mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_challenge=lambda: announcements.append(1),
    )

    assert minted.solved_challenge is True
    assert len(announcements) == 1


@pytest.mark.unit
def test_only_this_origins_cookies_leave_the_browser_profile(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The profile is a real browser's; unrelated sites' cookies are not ours to copy out."""
    _install_fake(
        monkeypatch,
        contents=[_REAL_PAGE],
        cookies=[
            _px_cookie(),
            {"name": "session", "value": "keep", "domain": "www.aruodas.lt"},
            {"name": "tracker", "value": "leave", "domain": ".doubleclick.net"},
        ],
    )
    clock = _FakeClock()

    minted = mint_cookie(
        profile_dir=tmp_path / "profile", sleeper=clock.advance, clock=clock, headless=True
    )

    assert minted.header == "_px3=abc123; session=keep"
    assert "leave" not in minted.header


@pytest.mark.unit
def test_the_browser_is_real_chrome_and_visible(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Headless Chrome names itself in its own User-Agent and cannot clear a challenge."""
    driver, _, _ = _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[_px_cookie()])
    clock = _FakeClock()

    mint_cookie(profile_dir=tmp_path / "profile", sleeper=clock.advance, clock=clock)

    assert driver.launch_kwargs["channel"] == "chrome"
    assert driver.launch_kwargs["headless"] is False


@pytest.mark.unit
def test_an_unsolved_challenge_times_out_saying_so(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake(monkeypatch, contents=[_CHALLENGE_PAGE], cookies=[])
    clock = _FakeClock()

    with pytest.raises(ConfigurationError, match="still unsolved"):
        mint_cookie(
            profile_dir=tmp_path / "profile",
            timeout_seconds=5.0,
            sleeper=clock.advance,
            clock=clock,
            headless=True,
        )


@pytest.mark.unit
def test_a_quiet_page_that_mints_nothing_names_that_cause_instead(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No challenge and no token is a different fault from an unsolved challenge."""
    _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[])
    clock = _FakeClock()

    with pytest.raises(ConfigurationError, match="did not run"):
        mint_cookie(
            profile_dir=tmp_path / "profile",
            timeout_seconds=5.0,
            sleeper=clock.advance,
            clock=clock,
            headless=True,
        )


@pytest.mark.unit
def test_the_browser_is_closed_even_when_minting_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leaked headed Chrome would hold the profile lock against the next attempt."""
    _, context, _ = _install_fake(monkeypatch, contents=[_CHALLENGE_PAGE], cookies=[])
    clock = _FakeClock()

    with pytest.raises(ConfigurationError):
        mint_cookie(
            profile_dir=tmp_path / "profile",
            timeout_seconds=5.0,
            sleeper=clock.advance,
            clock=clock,
            headless=True,
        )

    assert context.closed is True


@pytest.mark.unit
def test_a_minted_cookie_round_trips_through_the_file_the_scraper_reads(tmp_path: Path) -> None:
    """The writer and the loader must agree, since nothing else checks the handoff."""
    path = tmp_path / "cookie.txt"

    write_cookie_file(path, "_px3=abc123; session=keep")
    loaded = load_cookie_file(path)

    assert loaded is not None
    assert loaded.has_protection_token is True
    assert loaded.is_stale is False


@pytest.mark.unit
def test_an_empty_cookie_is_refused_rather_than_written(tmp_path: Path) -> None:
    """Overwriting a good cookie with nothing would cost a run before anyone noticed."""
    path = tmp_path / "cookie.txt"
    write_cookie_file(path, "_px3=good")

    with pytest.raises(ConfigurationError, match="empty"):
        write_cookie_file(path, "   ")

    assert path.read_text(encoding="utf-8") == "_px3=good"


@pytest.mark.unit
def test_writing_leaves_no_temporary_file_behind(tmp_path: Path) -> None:
    path = tmp_path / "cookie.txt"

    write_cookie_file(path, "_px3=abc123")

    assert [entry.name for entry in tmp_path.iterdir()] == ["cookie.txt"]


@pytest.mark.unit
def test_an_oversized_cookie_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="exceeds"):
        write_cookie_file(tmp_path / "cookie.txt", "_px3=" + "x" * 20000)
