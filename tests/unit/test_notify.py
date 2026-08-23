"""Unit tests for the challenge alert.

The alert is a convenience, so the property that matters is that it can never be the reason
a run fails: every path through it has to stay silent about its own failures.
"""

from __future__ import annotations

import sys
from typing import Any

import pytest

from aruodas_scraper import notify


@pytest.mark.unit
def test_alerting_reports_nothing_and_raises_nothing() -> None:
    """Runs for real on this host, whichever it is; the point is that it returns."""
    assert notify.alert_operator() is None


@pytest.mark.unit
def test_a_desktop_that_refuses_to_raise_the_window_is_not_an_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def explode() -> bool:
        raise OSError("no window server")

    monkeypatch.setattr(notify, "_raise_windows_window", explode)
    monkeypatch.setattr(sys, "platform", "win32")

    assert notify._raise_browser_window() is False


@pytest.mark.unit
def test_a_silent_host_does_not_stop_the_alert(monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing audio stack must not cost the window raise that follows the beep."""
    raised: list[bool] = []

    def explode(*_args: Any, **_kwargs: Any) -> None:
        raise RuntimeError("no audio device")

    monkeypatch.setattr(notify.sys.stdout, "write", explode)
    monkeypatch.setattr(notify, "_raise_browser_window", lambda: raised.append(True) or False)

    notify.alert_operator()

    assert raised == [True]


@pytest.mark.unit
def test_nothing_is_raised_away_from_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")

    assert notify._raise_browser_window() is False
