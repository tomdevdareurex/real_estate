"""
A challenge is the least observable moment in a run: it appears in a window nobody is
watching yet, is cleared by hand, and leaves nothing behind. When a mint later fails, or the
origin changes what it raises, there is no record of what was actually on screen - only that
the wait ran out.

"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


import sys
from playwright.sync_api import sync_playwright

HOLD_SECONDS = 10.0
DEFAULT_SETTLE_SECONDS = 2.0
_SETTLE_POLL_SECONDS = 0.2
_STAMP_FORMAT = "%Y%m%d-%H%M%S"
_UNLOADED_FRAME_URLS = {"", "about:blank"}
_FRAME_TEXT_BUDGET = 160

# def record_challenge(
#     directory: Path, *, settle_seconds: float = DEFAULT_SETTLE_SECONDS
# ) -> Callable[[Any], None]:
#     """Return an observer that targets the center of the red challenge capsule."""

#     def observe(page: Any) -> None:
#         directory.mkdir(parents=True, exist_ok=True)
#         settled = _wait_for_frames(page, settle_seconds)
#         stamp = datetime.now(timezone.utc).strftime(_STAMP_FORMAT)

#         _write_screenshot(page, directory / f"challenge-{stamp}.png")
#         _write_summary(page, directory / f"challenge-{stamp}.txt", stamp, settled=settled)

#         print("\n== Locating Challenge Target ==")

#         # 1. Target the outer iframe element on the top-level page
#         iframe = page.locator('iframe[title="Human verification challenge"]').first
#         iframe.wait_for(state="attached", timeout=15000)

#         # 2. Poll for the outer box layout
#         box = None
#         for _ in range(30):
#             box = iframe.bounding_box()
#             if box and box["width"] > 0:
#                 break
#             page.wait_for_timeout(100)

#         if box and box["width"] > 0:
#             centre_x = box["x"] + (box["width"] / 2)
#             # Reduced vertical offset to hit exact button center
#             centre_y = box["y"] + (box["height"] / 2) + 50
#             print(f"== Calculated Button Position: ({centre_x:.1f}, {centre_y:.1f}) ==")
#         else:
#             print("  Iframe box unavailable; targeting adjusted viewport center...")
#             viewport = page.viewport_size or {"width": 1280, "height": 720}
#             centre_x = viewport["width"] / 2
#             # Reduced offset to +50px relative to viewport center
#             centre_y = (viewport["height"] / 2) + 50

#         print(f"== Target Coordinates: ({centre_x:.1f}, {centre_y:.1f}) ==")

#         # 3. Draw visual marker on screen
#         page.evaluate(
#             """({x, y}) => {
#                 let dot = document.getElementById('debug-mouse-pointer');
#                 if (!dot) {
#                     dot = document.createElement('div');
#                     dot.id = 'debug-mouse-pointer';
#                     dot.style.position = 'fixed';
#                     dot.style.width = '24px';
#                     dot.style.height = '24px';
#                     dot.style.backgroundColor = 'red';
#                     dot.style.border = '3px solid white';
#                     dot.style.borderRadius = '50%';
#                     dot.style.zIndex = '2147483647';
#                     dot.style.pointerEvents = 'none';
#                     dot.style.transform = 'translate(-50%, -50%)';
#                     dot.style.boxShadow = '0 0 10px rgba(0,0,0,0.5)';
#                     document.body.appendChild(dot);
#                 }
#                 dot.style.left = `${x}px`;
#                 dot.style.top = `${y}px`;
#             }""",
#             {"x": centre_x, "y": centre_y},
#         )

#         print(f"== Holding button for {HOLD_SECONDS}s ==")

#         # 4. Move mouse and execute hold
#         page.mouse.move(centre_x, centre_y, steps=15)
#         page.mouse.down()
#         try:
#             page.wait_for_timeout(HOLD_SECONDS * 1000)
#         finally:
#             page.mouse.up()
import random
import time
from typing import Any, Callable
from pathlib import Path
from datetime import datetime, timezone

import random
from typing import Any, Callable
from pathlib import Path
from datetime import datetime, timezone

def record_challenge(
    directory: Path, *, settle_seconds: float = 2.0
) -> Callable[[Any], None]:
    """Return an observer that targets the center of the red challenge capsule."""

    def observe(page: Any) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        
        print("\n== Locating Challenge Target ==")

        # Outer loop: Try up to 3 times before giving up
        for attempt in range(1, 4):
            print(f"\n== Attempt {attempt} ==")

            # 1. Target the outer iframe element
            iframe = page.locator('iframe[title="Human verification challenge"]').first
            
            try:
                # If it doesn't attach within 5 seconds, it's likely gone/solved
                iframe.wait_for(state="attached", timeout=5000)
            except Exception:
                print("== No challenge iframe found. Challenge cleared! ==")
                break

            # 2. Poll for the outer box layout
            box = None
            for _ in range(30):
                box = iframe.bounding_box()
                if box and box["width"] > 0:
                    break
                page.wait_for_timeout(100)

            if box and box["width"] > 0:
                variance_x = random.uniform(-10, 10)
                variance_y = random.uniform(-5, 5)
                centre_x = box["x"] + (box["width"] / 2) + variance_x
                centre_y = box["y"] + (box["height"] / 2) + 50 + variance_y
            else:
                print("  Iframe box unavailable; targeting adjusted viewport center...")
                viewport = page.viewport_size or {"width": 1280, "height": 720}
                centre_x = (viewport["width"] / 2) + random.uniform(-15, 15)
                centre_y = (viewport["height"] / 2) + 50 + random.uniform(-10, 10)

            print(f"== Target Coordinates: ({centre_x:.1f}, {centre_y:.1f}) ==")

            # 3. Draw visual marker on screen
            page.evaluate(
                """({x, y}) => {
                    let dot = document.getElementById('debug-mouse-pointer');
                    if (!dot) {
                        dot = document.createElement('div');
                        dot.id = 'debug-mouse-pointer';
                        dot.style.position = 'fixed';
                        dot.style.width = '24px';
                        dot.style.height = '24px';
                        dot.style.backgroundColor = 'red';
                        dot.style.border = '3px solid white';
                        dot.style.borderRadius = '50%';
                        dot.style.zIndex = '2147483647';
                        dot.style.pointerEvents = 'none';
                        dot.style.transform = 'translate(-50%, -50%)';
                        dot.style.boxShadow = '0 0 10px rgba(0,0,0,0.5)';
                        document.body.appendChild(dot);
                    }
                    dot.style.left = `${x}px`;
                    dot.style.top = `${y}px`;
                }""",
                {"x": centre_x, "y": centre_y},
            )

            # 4. Move mouse (Simple curved approach)
            waypoint_x = centre_x + random.choice([-1, 1]) * random.uniform(50, 150)
            waypoint_y = centre_y + random.choice([-1, 1]) * random.uniform(20, 80)
            
            page.mouse.move(waypoint_x, waypoint_y, steps=random.randint(5, 12))
            page.wait_for_timeout(random.randint(20, 80)) 
            page.mouse.move(centre_x, centre_y, steps=random.randint(15, 25))
            page.wait_for_timeout(random.randint(100, 300))

            print("== Holding button strictly for 10 seconds ==")

            # 5. Execute hard 10-second hold (with micro-movements to avoid bot detection)
            page.mouse.down()
            try:
                # 20 loops * 0.5s sleep = 10 seconds total hold time
                for _ in range(20):
                    jitter_x = centre_x + random.uniform(-2, 2)
                    jitter_y = centre_y + random.uniform(-2, 2)
                    page.mouse.move(jitter_x, jitter_y, steps=2)
                    page.wait_for_timeout(500)
            finally:
                page.mouse.up()
            
            print("== Released. Waiting to check if challenge remains... ==")
            
            # 6. Wait a random moment (2 to 4 seconds) to let Cloudflare load the success state
            page.wait_for_timeout(random.randint(2000, 4000))
            
            # If the box is still taking up space on the screen, the loop restarts
            if not iframe.is_visible():
                print("== Challenge solved successfully! ==")
                break

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
