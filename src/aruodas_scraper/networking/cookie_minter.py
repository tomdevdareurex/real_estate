"""Minting a high-trust bot-protection cookie with a real browser.

The request ceiling is set by *how* the `_px3` cookie was minted, far more than by how fresh
it is. Measured 2026-08-22: a cookie copied from an ordinary browse was spent after about 6
requests, while one taken after a human solved the interactive challenge carried the same run
past 100 without a refusal. The bot-protection layer treats a solved challenge as evidence of
a human and raises the budget accordingly, so "has a `_px3`" is the wrong test for a good
cookie - two cookies with that name can differ by more than an order of magnitude in worth.

That is why this module drives a real, visible Chrome instead of fetching a page. The solve
itself is deliberately not automated: it is the human attestation the elevated budget is
paying for, and forging it would both defeat the point and breach the authorization this
project runs under. Everything *around* it is automated - launching on a profile that
remembers previous solves, waiting for the challenge to clear, harvesting the cookie, and
handing it back for writing where the scraper already looks.

Two settings are load-bearing:

- **Headed.** Headless Chrome announces itself in its own User-Agent (`HeadlessChrome/151`)
  and fails cheap sensor probes besides, so it draws a challenge it then cannot clear. The
  `headless` argument exists for tests, not for operators.
- **A persistent profile.** The solve is remembered there, so later mints usually find the
  challenge already satisfied and need no interaction at all. That is what turns an hourly
  copy-paste chore into an occasional click. The profile holds live session credentials and
  belongs outside the repository, next to the cookie file itself.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.networking.cookie_source import PROTECTION_COOKIE_NAME

# A search page rather than the site root: it is a page the scraper itself requests, so the
# challenge raised here is the one the run would have met, and clearing it is on the path
# that matters. It costs a single request.
DEFAULT_MINT_URL = "https://www.aruodas.lt/butai-vilniuje/"

# Generous, because the budget of the whole next run rides on the human getting to the window.
DEFAULT_MINT_TIMEOUT_SECONDS = 300.0

_POLL_SECONDS = 1.0

# Shares the marker the HTTP client keys on. `_pxAppId` appears on ordinary successful pages
# too, as the sensor script, so matching on that would read every good page as a challenge.
_CHALLENGE_MARKER = "px-captcha"

_COOKIE_DOMAIN_SUFFIX = "aruodas.lt"

_INSTALL_HINT = (
    "Minting a cookie needs Playwright, which is an optional extra: install it with\n"
    "    .venv/Scripts/python.exe -m pip install playwright\n"
    "It drives the Chrome already installed on this machine, so no browser download is "
    "required."
)


@dataclass(frozen=True, slots=True)
class MintedCookie:
    """A Cookie header taken from a real browser session, with how it was obtained."""

    header: str
    solved_challenge: bool

    def describe(self) -> str:
        """Summarise the cookie without disclosing it; the value authenticates this client."""
        origin = "after a solved challenge" if self.solved_challenge else "from a live session"
        return f"{len(self.header)} bytes, {origin}"


def _load_playwright() -> Any:
    """Import Playwright on demand so the scraper still runs when it is absent."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:  # pragma: no cover - exercised only without the extra
        raise ConfigurationError(_INSTALL_HINT) from error
    return sync_playwright


def _site_cookies(context: Any) -> list[dict[str, Any]]:
    """Return only this origin's cookies, so nothing unrelated is copied out of the profile."""
    return [
        cookie
        for cookie in context.cookies()
        if str(cookie.get("domain", "")).lstrip(".").endswith(_COOKIE_DOMAIN_SUFFIX)
    ]


def _cookie_header(cookies: Iterable[dict[str, Any]]) -> str:
    """Format cookies the way a browser's own Cookie request header would."""
    return "; ".join(f"{cookie['name']}={cookie['value']}" for cookie in cookies)


def _page_shows_challenge(page: Any) -> bool:
    """Whether the page is currently a challenge.

    A navigation in flight makes `content()` raise. That is not an error to report: it means
    the page has not answered yet, which is exactly what the caller's next poll is for.
    """
    try:
        return _CHALLENGE_MARKER in page.content()
    except Exception:
        return False


def mint_cookie(
    *,
    profile_dir: Path,
    url: str = DEFAULT_MINT_URL,
    timeout_seconds: float = DEFAULT_MINT_TIMEOUT_SECONDS,
    headless: bool = False,
    on_challenge: Callable[[], None] | None = None,
    sleeper: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> MintedCookie:
    """Open a browser, wait until the origin is satisfied, and return its Cookie header.

    Args:
        profile_dir: Persistent Chrome profile. Remembers previous solves, so a later mint
            usually needs no interaction. Holds live credentials; keep it outside the repo.
        url: Page to load. Costs one request against the budget.
        timeout_seconds: How long to wait for a challenge to be cleared.
        headless: For tests only. Headless Chrome cannot clear a challenge.
        on_challenge: Called once, when a challenge is first seen, to prompt the operator.
        sleeper: Injected so tests need not wait.
        clock: Monotonic time source, injected for the same reason.

    Returns:
        The harvested cookie, and whether a challenge was solved to get it.

    Raises:
        ConfigurationError: If Playwright is missing, or the wait ran out.
    """
    sync_playwright = _load_playwright()
    profile_dir.mkdir(parents=True, exist_ok=True)
    deadline = clock() + timeout_seconds
    was_challenged = False

    with sync_playwright() as driver:
        context = driver.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            channel="chrome",
            headless=headless,
            # Let the window size itself; a scripted viewport is one more thing that differs
            # from the browser this is supposed to be.
            viewport=None,
            args=["--disable-blink-features=AutomationControlled"],
        )
        try:
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(url, wait_until="domcontentloaded")
            while True:
                challenged = _page_shows_challenge(page)
                if challenged and not was_challenged:
                    was_challenged = True
                    if on_challenge is not None:
                        on_challenge()
                if not challenged:
                    cookies = _site_cookies(context)
                    if any(cookie["name"] == PROTECTION_COOKIE_NAME for cookie in cookies):
                        return MintedCookie(
                            header=_cookie_header(cookies),
                            solved_challenge=was_challenged,
                        )
                if clock() >= deadline:
                    raise ConfigurationError(_timeout_message(challenged, timeout_seconds))
                sleeper(_POLL_SECONDS)
        finally:
            context.close()


def _timeout_message(still_challenged: bool, timeout_seconds: float) -> str:
    """Explain a timeout in terms of what the operator saw, since the two causes differ."""
    if still_challenged:
        return (
            f"The challenge was still unsolved after {timeout_seconds:.0f}s. Solve it in the "
            "browser window that opened, then run this command again."
        )
    return (
        f"No {PROTECTION_COOKIE_NAME} cookie appeared within {timeout_seconds:.0f}s. The page "
        "loaded without raising a challenge and without minting a token, which usually means "
        "the sensor script did not run - check that the window reached a real listing page."
    )


__all__ = [
    "DEFAULT_MINT_TIMEOUT_SECONDS",
    "DEFAULT_MINT_URL",
    "MintedCookie",
    "mint_cookie",
]
