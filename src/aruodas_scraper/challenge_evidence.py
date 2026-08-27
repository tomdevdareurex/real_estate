"""Read-only capture of whatever the origin raises in a mint window.

A challenge is the least observable moment in a run: it appears in a window nobody is
watching yet, is cleared by hand, and leaves nothing behind. When a mint later fails, or the
origin changes what it raises, there is no record of what was actually on screen - only that
the wait ran out. This module writes that record: a screenshot and a frame-by-frame summary,
every time a challenge is raised.

Nothing here touches the challenge. It is not clicked, focused, scrolled or answered, and it
must stay that way: the solve is the human attestation the raised request budget pays for, so
automating it would both falsify that attestation and put the project outside what it is
authorized to do. See AGENTS.md, 2026-08-22. The capture only ever reads.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

DEFAULT_SETTLE_SECONDS = 2.0
_SETTLE_POLL_SECONDS = 0.2
_STAMP_FORMAT = "%Y%m%d-%H%M%S"
_UNLOADED_FRAME_URLS = {"", "about:blank"}
_FRAME_TEXT_BUDGET = 160


def record_challenge(
    directory: Path, *, settle_seconds: float = DEFAULT_SETTLE_SECONDS
) -> Callable[[Any], None]:
    """Return an observer that writes a screenshot and a summary of what was raised.

    Args:
        directory: Where the pair of files lands; created on demand. Keep it out of the
            repository - a capture is a picture of a live session.
        settle_seconds: How long to give a sub-frame to report a real URL before capturing
            anyway. The hook fires before the widget has drawn, so a capture taken at once
            is an artefact of the timing rather than a finding. Zero disables the wait.

    Returns:
        A callable taking a Playwright page, suitable as a challenge observer.
    """

    def observe(page: Any) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        settled = _wait_for_frames(page, settle_seconds)
        stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)

        _write_screenshot(page, directory / f"challenge-{stamp}.png")
        _write_summary(page, directory / f"challenge-{stamp}.txt", stamp, settled=settled)

    return observe


def _wait_for_frames(page: Any, settle_seconds: float) -> bool:
    """Wait until a sub-frame reports a real URL; report whether one ever did.

    The answer is recorded either way, and that is the point: a frame still blank after the
    full wait is a finding about the challenge (its widget is written into a blank document
    rather than navigated), not a capture taken too early. Without the wait the two are
    indistinguishable.
    """
    waited = 0.0
    while True:
        if any(frame.url not in _UNLOADED_FRAME_URLS for frame in page.frames[1:]):
            return True
        if waited >= settle_seconds:
            return False
        page.wait_for_timeout(_SETTLE_POLL_SECONDS * 1000)
        waited += _SETTLE_POLL_SECONDS


def _write_screenshot(page: Any, path: Path) -> None:
    """Capture the window, landing the file atomically like every other write here."""
    temporary = path.with_suffix(".png.tmp")
    # `type` has to be explicit: Playwright otherwise infers the format from the extension
    # and rejects `.tmp` outright, which is the price of writing atomically here.
    page.screenshot(path=str(temporary), full_page=True, type="png")
    os.replace(temporary, path)


def _clip(text: str) -> str:
    """Keep the summary readable; a whole document pasted into it is not evidence."""
    return text if len(text) <= _FRAME_TEXT_BUDGET else text[:_FRAME_TEXT_BUDGET] + "..."


def _read_text(frame: Any) -> str | None:
    """The frame's rendered text, or None when it cannot be read.

    Reading is passive: no event is dispatched, nothing is focused or scrolled. A widget is
    indistinguishable from the page merely being open.
    """
    try:
        return " ".join(frame.locator("body").inner_text().split())
    except Exception:  # A frame can detach mid-capture; that is data, not a failure.
        return None


def _classify(index: int, frame: Any, text: str | None) -> str:
    """Name what a frame is for, from what it shows rather than from its address.

    Text is the honest discriminator. A challenge has to tell a person what to do, so the one
    frame carrying an instruction is the widget - no matter which vendor domain serves it, and
    without depending on ids that are regenerated every render. Frames that display nothing
    are doing something else: the loaded ones carry sensor script, and the blank ones are
    environment probes, created so a script can compare a pristine set of built-ins against
    the ones in the top document and notice if any were patched.
    """
    if index == 0:
        return "main"
    if text is None:
        return "unreadable"
    if text:
        return "widget"
    return "probe" if frame.url in _UNLOADED_FRAME_URLS else "script"


def _frame_report(page: Any) -> tuple[list[str], str | None, str | None]:
    """Describe every frame, and pick out the one a person is being asked to act on.

    Returns the report lines, the widget frame's URL, and the instruction it displays.
    """
    lines: list[str] = []
    widget_url: str | None = None
    prompt: str | None = None
    for index, frame in enumerate(page.frames):
        text = _read_text(frame)
        role = _classify(index, frame, text)
        # `role == "widget"` already implies non-empty text; the check keeps mypy honest.
        if role == "widget" and widget_url is None and text is not None:
            widget_url, prompt = frame.url, _clip(text)
        shown = _clip(text if text else ("<unreadable>" if text is None else "<empty>"))
        lines.append(
            f"  [{role}] name={frame.name!r} url={frame.url}" + chr(10) + f"      text: {shown}"
        )
    return lines, widget_url, prompt


def _write_summary(page: Any, path: Path, stamp: str, *, settled: bool) -> None:
    """Describe the page in the terms that tell a challenge from a failed load."""
    frames, widget_url, prompt = _frame_report(page)
    # The site's own heading is absent for as long as the interstitial is up, so its count is
    # a one-line answer to "was this a challenge, or did the page simply not arrive?".
    site_markup_present = page.locator("h1").count() > 0
    lines = [
        f"captured_utc: {stamp}",
        f"url:          {page.url}",
        f"title:        {page.title()}",
        f"top_document_bytes: {len(page.content())}",
        f"site_markup_present: {site_markup_present}",
        f"frame_count:  {len(page.frames)}",
        f"frames_settled: {settled}",
        # The two lines that answer "is there something here a person could solve, or is this
        # a refusal with no way through?" - which decides whether waiting is the only option.
        f"challenge_widget: {widget_url or 'none found'}",
        f"challenge_prompt: {prompt or 'none'}",
        "frames:",
        *frames,
        "",
    ]
    temporary = path.with_suffix(".txt.tmp")
    temporary.write_text("\n".join(lines), encoding="utf-8")
    os.replace(temporary, path)


__all__ = ["DEFAULT_SETTLE_SECONDS", "record_challenge"]
