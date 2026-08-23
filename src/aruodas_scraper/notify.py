"""Getting the operator's attention when a mint is waiting on a human.

`solve_on_block` turns a 25-minute cooldown into a click, but only if the click happens. The
browser opens mid-run, often while the operator is looking at something else, and the window
that needs them can end up behind whatever they were doing. A run then sits in its 300s wait
and falls back to the cooldown it was trying to avoid - the wait is not the expensive part,
the missed window is.

So this rings a bell and asks the desktop to bring the browser forward. Both are best effort
by nature: Windows refuses foreground changes from a process that is not already foreground,
which is why the taskbar flash is requested as well - that one is always permitted, and a
blinking taskbar button survives the operator being in another room.

Nothing here is load-bearing. Every failure is logged at debug and swallowed: losing a
notification is a small cost, and letting it end a run that has a valid cookie waiting is not.
"""

from __future__ import annotations

import ctypes
import logging
import sys
from typing import Any

logger = logging.getLogger(__name__)

# The window class every Chrome top-level window carries. Matching on it rather than on the
# title keeps this working while the tab title changes, which it does the moment a challenge
# is raised and again once the page behind it loads.
_CHROME_WINDOW_CLASS = "Chrome_WidgetWin_1"

_SW_RESTORE = 9
_FLASHW_ALL = 0x00000003
_FLASHW_TIMERNOFG = 0x0000000C
_FLASH_COUNT = 5


def alert_operator() -> None:
    """Ring, then try to pull the browser window in front. Never raises."""
    _beep()
    _raise_browser_window()


def _beep() -> None:
    """Make a noise the operator will hear from outside the terminal."""
    try:
        if sys.platform == "win32":
            import winsound

            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
        else:
            # The terminal bell is the only portable equivalent, and it is enough.
            sys.stdout.write("\a")
            sys.stdout.flush()
    except Exception as error:  # pragma: no cover - depends on the host's audio stack
        logger.debug("Could not sound the alert: %s", error)


def _raise_browser_window() -> bool:
    """Bring the mint browser forward if the desktop allows it; report whether it worked.

    Returns False on every platform but Windows, and whenever the window cannot be found or
    the foreground change is refused. A False here is not a problem: the bell has already
    rung and the taskbar has been asked to flash.
    """
    if sys.platform != "win32":
        return False
    try:
        return _raise_windows_window()
    except Exception as error:  # pragma: no cover - ctypes failures are host-specific
        logger.debug("Could not raise the browser window: %s", error)
        return False


def _raise_windows_window() -> bool:
    """Find Chrome's top-level window and ask Windows to put it in front.

    Everything Win32 is reached through `getattr`, and the structures are built from plain
    ctypes types rather than `ctypes.wintypes`: importing that module fails outright on
    Linux, where this file still has to import cleanly for CI to type-check it.
    """
    import ctypes

    user32: Any = getattr(ctypes, "windll").user32
    prototype: Any = getattr(ctypes, "WINFUNCTYPE")(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    found: list[int] = []

    def visit(handle: int, _param: int) -> bool:
        if not user32.IsWindowVisible(handle):
            return True
        class_name = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(handle, class_name, 256)
        if class_name.value == _CHROME_WINDOW_CLASS and user32.GetWindowTextLengthW(handle):
            found.append(handle)
            return False  # Stop at the first one; a persistent profile opens exactly one.
        return True

    user32.EnumWindows(prototype(visit), 0)
    if not found:
        return False
    handle = found[0]

    # Always ask for the flash first. Windows grants it unconditionally, so the operator gets
    # a blinking taskbar button even when the foreground change below is refused.
    _flash(user32, handle)

    # Windows only lets the current foreground process hand focus away. Borrowing its input
    # queue for the length of the call is the documented way around that; without it
    # SetForegroundWindow silently does nothing when the terminal is not already in front.
    foreground = user32.GetForegroundWindow()
    ours = user32.GetWindowThreadProcessId(foreground, None)
    theirs = user32.GetWindowThreadProcessId(handle, None)
    attached = bool(user32.AttachThreadInput(ours, theirs, True))
    try:
        user32.ShowWindow(handle, _SW_RESTORE)  # Undo a minimise, if any.
        user32.BringWindowToTop(handle)
        return bool(user32.SetForegroundWindow(handle))
    finally:
        if attached:
            user32.AttachThreadInput(ours, theirs, False)


class _FlashInfo(ctypes.Structure):
    """FLASHWINFO, laid out with portable ctypes types so this imports away from Windows."""

    _fields_ = [
        ("cbSize", ctypes.c_uint),
        ("hwnd", ctypes.c_void_p),
        ("dwFlags", ctypes.c_ulong),
        ("uCount", ctypes.c_uint),
        ("dwTimeout", ctypes.c_ulong),
    ]


def _flash(user32: Any, handle: int) -> None:
    """Blink the taskbar button until the window is looked at."""
    info = _FlashInfo(
        ctypes.sizeof(_FlashInfo),
        handle,
        _FLASHW_ALL | _FLASHW_TIMERNOFG,
        _FLASH_COUNT,
        0,
    )
    user32.FlashWindowEx(ctypes.byref(info))


__all__ = ["alert_operator"]
