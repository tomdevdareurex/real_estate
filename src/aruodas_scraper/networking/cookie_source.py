"""Loading and describing the borrowed browser session cookie.

The bot-protection token is minted by a JavaScript sensor this client cannot run, so the only
way to hold one is to copy it out of a signed-in browser on the same address. That copy
expires, and an expired one is indistinguishable from no cookie at all: both simply produce a
lower request ceiling. This module makes that difference visible before a run spends an hour
discovering it.
"""

from __future__ import annotations

import os
import secrets
import time
from dataclasses import dataclass
from pathlib import Path

from aruodas_scraper.exceptions import ConfigurationError

MAX_COOKIE_BYTES = 16384

# The cookie PerimeterX mints for a scored session. Without it the header is just an ordinary
# Aruodas session and buys nothing against the bot-protection layer.
PROTECTION_COOKIE_NAME = "_px3"

# Warn past an hour. The file's mtime records when the header was copied, not when the origin
# issued it, so this is a floor on the cookie's real age rather than a measurement of it.
# PerimeterX rotates its token on roughly this timescale, so an older copy is likely spent.
STALE_AFTER_SECONDS = 3600.0


@dataclass(frozen=True, slots=True)
class BrowserCookie:
    """A Cookie header copied from a browser, with the facts needed to judge it."""

    header: str
    age_seconds: float
    has_protection_token: bool

    @property
    def is_stale(self) -> bool:
        """Whether the copy is old enough that the origin has probably rotated it."""
        return self.age_seconds >= STALE_AFTER_SECONDS

    def describe(self) -> str:
        """Summarise the cookie without disclosing it; the value authenticates this client."""
        token = "present" if self.has_protection_token else f"NO {PROTECTION_COOKIE_NAME}"
        staleness = ", likely expired" if self.is_stale else ""
        return f"{len(self.header)} bytes, {self.age_seconds / 60:.0f} min old, {token}{staleness}"


def _cookie_names(header: str) -> frozenset[str]:
    """Return the names in a Cookie header, ignoring values so none is matched by accident."""
    return frozenset(pair.split("=", 1)[0].strip() for pair in header.split(";") if "=" in pair)


def load_cookie_file(path: Path | None, now: float | None = None) -> BrowserCookie | None:
    """Read a Cookie header from disk and describe it.

    Args:
        path: File holding a Cookie header, or ``None`` to run without one.
        now: Current epoch time. Injected for tests, so age is not wall-clock dependent.

    Returns:
        The loaded cookie, or ``None`` when no path was given.

    Raises:
        ConfigurationError: If the file is missing, unreadable, empty, or oversized.
    """
    if path is None:
        return None
    try:
        stat = path.stat()
        if stat.st_size > MAX_COOKIE_BYTES:
            raise ConfigurationError(f"Cookie file exceeds {MAX_COOKIE_BYTES} bytes: {path}")
        header = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as error:
        raise ConfigurationError(f"Cookie file was not found: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Cookie file could not be read: {path}") from error
    if not header:
        raise ConfigurationError(f"Cookie file is empty: {path}")
    return BrowserCookie(
        header=header,
        # A clock skewed against the file's mtime could make this negative, which would read
        # as a suspiciously fresh cookie rather than an obviously wrong one.
        age_seconds=max(0.0, (time.time() if now is None else now) - stat.st_mtime),
        has_protection_token=PROTECTION_COOKIE_NAME in _cookie_names(header),
    )


def write_cookie_file(path: Path, header: str) -> None:
    """Replace the cookie file atomically, so no run ever reads a half-written header.

    A partial header is worse than a missing one: it still parses, still looks like a cookie,
    and simply buys a lower ceiling that would be blamed on the origin.

    Raises:
        ConfigurationError: If the header is empty, oversized, or cannot be written.
    """
    header = header.strip()
    if not header:
        raise ConfigurationError("Refusing to write an empty cookie file")
    encoded = header.encode("utf-8")
    if len(encoded) > MAX_COOKIE_BYTES:
        raise ConfigurationError(f"Cookie exceeds {MAX_COOKIE_BYTES} bytes: {path}")
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with temporary.open("xb") as file_handle:
            file_handle.write(encoded)
        os.replace(temporary, path)
    except OSError as error:
        temporary.unlink(missing_ok=True)
        raise ConfigurationError(f"Cookie file could not be written: {path}") from error


__all__ = [
    "MAX_COOKIE_BYTES",
    "PROTECTION_COOKIE_NAME",
    "STALE_AFTER_SECONDS",
    "BrowserCookie",
    "load_cookie_file",
    "write_cookie_file",
]
