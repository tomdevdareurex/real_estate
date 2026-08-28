"""Unit tests for browser-minted bot-protection cookies."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

import pytest

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.networking import cookie_minter
from aruodas_scraper.networking.cookie_minter import mint_cookie, press_and_hold
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


class _FakeMouse:
    """Records pointer calls in order, so a test can assert the button was really held."""

    def __init__(self, journal: list[tuple[str, Any]]) -> None:
        self._journal = journal

    def move(self, x: float, y: float) -> None:
        self._journal.append(("move", (x, y)))

    def down(self) -> None:
        self._journal.append(("down", None))

    def up(self) -> None:
        self._journal.append(("up", None))


class _FakeLocator:
    def __init__(self, box: dict[str, float] | None, error: Exception | None) -> None:
        self._box = box
        self._error = error

    def wait_for(self, **_: Any) -> None:
        if self._error is not None:
            raise self._error

    def bounding_box(self) -> dict[str, float] | None:
        return self._box


class _FakePage:
    def __init__(self, contents: list[str]) -> None:
        self._contents = contents
        self.visited: list[str] = []
        self.journal: list[tuple[str, Any]] = []
        self.mouse = _FakeMouse(self.journal)
        self.box: dict[str, float] | None = {"x": 10.0, "y": 20.0, "width": 4.0, "height": 6.0}
        self.locator_error: Exception | None = None

    def locator(self, selector: str) -> _FakeLocator:
        self.journal.append(("locator", selector))
        return _FakeLocator(self.box, self.locator_error)

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.journal.append(("wait", milliseconds))

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
def test_the_browser_is_visible(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Headless Chrome names itself in its own User-Agent and cannot clear a challenge."""
    driver, _, _ = _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[_px_cookie()])
    clock = _FakeClock()

    mint_cookie(profile_dir=tmp_path / "profile", sleeper=clock.advance, clock=clock)

    assert driver.launch_kwargs["headless"] is False


@pytest.mark.unit
def test_the_browser_is_playwrights_own_chromium(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Not `channel="chrome"`, and the omission is load-bearing rather than an oversight.

    This workstation sets the `RemoteDebuggingAllowed=0` policy for Google Chrome and Edge,
    which kills the `--remote-debugging-pipe` Playwright drives the browser over: the window
    opens, the connection never arrives, and the launch times out. The policy does not name
    Chromium, so the bundled build is the only one that can be driven here. Asserting the
    absence keeps a future "surely it should use real Chrome" from silently reintroducing it.
    """
    driver, _, _ = _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[_px_cookie()])
    clock = _FakeClock()

    mint_cookie(profile_dir=tmp_path / "profile", sleeper=clock.advance, clock=clock)

    assert "channel" not in driver.launch_kwargs


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


@pytest.mark.unit
def test_the_ready_hook_receives_the_page_before_the_browser_closes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _, context, page = _install_fake(monkeypatch, contents=[_REAL_PAGE], cookies=[_px_cookie()])
    clock = _FakeClock()
    seen: list[Any] = []

    def record(ready_page: Any) -> None:
        assert context.closed is False, "the hook must run while the window is still open"
        seen.append(ready_page)

    minted = mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_ready=record,
    )

    assert seen == [page]
    assert minted.header == "_px3=abc123"
    assert context.closed is True


@pytest.mark.unit
def test_the_ready_hook_does_not_run_while_a_challenge_is_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A challenge replaces the site's markup, so there is nothing to drive until it clears."""
    _install_fake(
        monkeypatch,
        contents=[_CHALLENGE_PAGE, _CHALLENGE_PAGE, _REAL_PAGE],
        cookies=[_px_cookie()],
    )
    clock = _FakeClock()
    calls: list[str] = []

    mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_challenge=lambda: calls.append("challenge"),
        on_ready=lambda _page: calls.append("ready"),
    )

    assert calls == ["challenge", "ready"]


@pytest.mark.unit
def test_press_and_hold_keeps_the_button_down_for_the_requested_time() -> None:
    page = _FakePage([_REAL_PAGE])

    press_and_hold(page, "#TZWGtwSIEtlPoPl", seconds=2.0)

    assert page.journal == [
        ("locator", "#TZWGtwSIEtlPoPl"),
        ("move", (12.0, 23.0)),
        ("down", None),
        ("wait", 2000.0),
        ("up", None),
    ]


@pytest.mark.unit
def test_press_and_hold_releases_the_button_even_when_the_wait_fails() -> None:
    """A button left down would break every interaction after it, including the human's."""
    page = _FakePage([_REAL_PAGE])

    def explode(_milliseconds: float) -> None:
        raise RuntimeError("navigated away mid-hold")

    page.wait_for_timeout = explode  # type: ignore[method-assign]

    with pytest.raises(RuntimeError):
        press_and_hold(page, "#target")

    assert page.journal[-1] == ("up", None)


@pytest.mark.unit
def test_press_and_hold_reports_a_missing_element_as_configuration() -> None:
    page = _FakePage([_REAL_PAGE])
    page.locator_error = TimeoutError("no such element")

    with pytest.raises(ConfigurationError, match="#gone"):
        press_and_hold(page, "#gone")


@pytest.mark.unit
def test_the_configured_hold_reaches_press_and_hold_through_the_renewer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The seconds set in the config have to survive the whole path to the mouse."""
    from aruodas_scraper import cli

    page = _FakePage([_REAL_PAGE])
    hold = cli._build_hold("#target", 3.5)

    hold(page)

    assert ("wait", 3500.0) in page.journal


@pytest.mark.unit
def test_a_hold_on_a_missing_element_does_not_cost_the_renewal() -> None:
    """The cookie is already earned by then; losing it would buy a 25-minute cooldown."""
    from aruodas_scraper import cli

    page = _FakePage([_REAL_PAGE])
    page.locator_error = TimeoutError("gone")

    cli._build_hold("#gone", 2.0)(page)  # must not raise

    assert ("down", None) not in page.journal


@pytest.mark.unit
def test_the_challenge_observer_gets_the_page_while_the_challenge_is_up(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """It has to fire on the challenge pass, before the page behind it has loaded."""
    _install_fake(
        monkeypatch,
        contents=[_CHALLENGE_PAGE, _CHALLENGE_PAGE, _REAL_PAGE],
        cookies=[_px_cookie()],
    )
    clock = _FakeClock()
    seen: list[str] = []

    mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_challenge_page=lambda page: seen.append(page.content()),
        on_ready=lambda page: seen.append("ready"),
    )

    assert seen == [_CHALLENGE_PAGE, "ready"]


@pytest.mark.unit
def test_the_challenge_observer_runs_once_however_long_the_solve_takes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _install_fake(
        monkeypatch,
        contents=[_CHALLENGE_PAGE] * 5 + [_REAL_PAGE],
        cookies=[_px_cookie()],
    )
    clock = _FakeClock()
    calls: list[int] = []

    mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_challenge_page=lambda _page: calls.append(1),
    )

    assert len(calls) == 1


@pytest.mark.unit
def test_a_failing_challenge_observer_does_not_close_the_window_on_the_operator(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The solve in progress is worth more than the observer that broke."""
    _, context, _ = _install_fake(
        monkeypatch,
        contents=[_CHALLENGE_PAGE, _REAL_PAGE],
        cookies=[_px_cookie()],
    )
    clock = _FakeClock()

    def explode(_page: Any) -> None:
        raise RuntimeError("screenshot path is not writable")

    minted = mint_cookie(
        profile_dir=tmp_path / "profile",
        sleeper=clock.advance,
        clock=clock,
        headless=True,
        on_challenge_page=explode,
    )

    assert minted.header == "_px3=abc123"
    assert minted.solved_challenge is True
    assert context.closed is True
