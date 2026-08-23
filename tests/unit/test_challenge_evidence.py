"""Unit tests for the read-only challenge capture."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from aruodas_scraper.challenge_evidence import record_challenge

_CHALLENGE_HTML = "<html><body><div id='px-captcha'></div></body></html>"


class _FakeBody:
    def __init__(self, text: str) -> None:
        self._text = text

    def inner_text(self) -> str:
        return self._text


class _FakeFrame:
    def __init__(self, name: str, url: str, text: str | None = "") -> None:
        self.name = name
        self.url = url
        self.text = text

    def locator(self, _selector: str) -> _FakeBody:
        if self.text is None:
            raise RuntimeError("frame detached")
        return _FakeBody(self.text)


class _FakeLocator:
    def __init__(self, count: int) -> None:
        self._count = count

    def count(self) -> int:
        return self._count


class _FakePage:
    """Only the read-only surface the observer is allowed to touch."""

    def __init__(self, heading_count: int = 0, loads_after_wait: bool = False) -> None:
        self.url = "https://www.aruodas.lt/butai-vilniuje/"
        self._heading_count = heading_count
        self.frames = [
            _FakeFrame("", "https://www.aruodas.lt/butai-vilniuje/"),
            _FakeFrame("", "about:blank"),
        ]
        self.screenshots: list[str] = []
        self.waits: list[float] = []
        self.loads_after_wait = loads_after_wait

    def wait_for_timeout(self, milliseconds: float) -> None:
        self.waits.append(milliseconds)
        # A frame that is going to load does so after the first wait.
        if self.loads_after_wait:
            self.frames[1].url = "https://captcha.px-cloud.net/challenge"

    def title(self) -> str:
        return "Access denied"

    def content(self) -> str:
        return _CHALLENGE_HTML

    def locator(self, _selector: str) -> _FakeLocator:
        return _FakeLocator(self._heading_count)

    def screenshot(self, path: str, **options: Any) -> None:
        # Playwright infers the image format from the extension unless `type` is
        # given, and errors on anything it does not recognise. Reproduced here
        # because the atomic write uses a `.tmp` path, which real Playwright refuses.
        if "type" not in options and not path.endswith((".png", ".jpeg", ".jpg")):
            raise ValueError("path: unsupported mime type " + Path(path).suffix)
        self.screenshots.append(path)
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")


@pytest.mark.unit
def test_a_capture_writes_both_a_screenshot_and_a_summary(tmp_path: Path) -> None:
    page = _FakePage()

    record_challenge(tmp_path / "evidence")(page)

    written = sorted(p.suffix for p in (tmp_path / "evidence").iterdir())
    assert written == [".png", ".txt"]


@pytest.mark.unit
def test_the_summary_records_the_frame_that_carries_the_challenge(tmp_path: Path) -> None:
    """The cross-origin frame is the whole point of the capture."""
    page = _FakePage(loads_after_wait=True)

    record_challenge(tmp_path)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "captcha.px-cloud.net" in summary
    assert "frame_count:  2" in summary
    assert "site_markup_present: False" in summary


@pytest.mark.unit
def test_no_temporary_files_are_left_behind(tmp_path: Path) -> None:
    """Both writes land atomically, like every other write in this project."""
    record_challenge(tmp_path)(_FakePage())

    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.unit
def test_a_loaded_page_is_recorded_as_carrying_site_markup(tmp_path: Path) -> None:
    """Distinguishes a challenge from a page that simply failed to arrive."""
    record_challenge(tmp_path)(_FakePage(heading_count=1))

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "site_markup_present: True" in summary


@pytest.mark.unit
def test_the_directory_is_created_on_demand(tmp_path: Path) -> None:
    target = tmp_path / "nested" / "evidence"

    record_challenge(target)(_FakePage())

    assert target.is_dir()


@pytest.mark.unit
def test_a_frame_that_loads_is_waited_for_and_recorded(tmp_path: Path) -> None:
    """The hook fires before the widget has drawn, so a capture taken at once is an artefact."""
    page = _FakePage(loads_after_wait=True)

    record_challenge(tmp_path)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "captcha.px-cloud.net" in summary
    assert "frames_settled: True" in summary


@pytest.mark.unit
def test_a_frame_that_never_loads_is_recorded_as_such_after_the_full_wait(
    tmp_path: Path,
) -> None:
    """A blank frame after waiting is a finding, not a mistimed capture - say which."""
    page = _FakePage(loads_after_wait=False)

    record_challenge(tmp_path, settle_seconds=1.0)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "frames_settled: False" in summary
    assert "about:blank" in summary
    assert sum(page.waits) == pytest.approx(1000.0)


@pytest.mark.unit
def test_waiting_can_be_turned_off(tmp_path: Path) -> None:
    page = _FakePage(loads_after_wait=True)

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    assert page.waits == []


@pytest.mark.unit
def test_each_frames_text_is_recorded_so_a_challenge_names_itself(tmp_path: Path) -> None:
    """What a widget says identifies it without any selector, id or markup knowledge."""
    page = _FakePage()
    page.frames[1].text = "Palaikykite nuspaude, kol pasikeis spalva"

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "text: Palaikykite nuspaude, kol pasikeis spalva" in summary
    assert "challenge_prompt: Palaikykite nuspaude, kol pasikeis spalva" in summary
    assert "[widget]" in summary


@pytest.mark.unit
def test_a_silent_frame_is_recorded_as_empty_rather_than_omitted(tmp_path: Path) -> None:
    """A frame that is present but says nothing is itself a finding."""
    page = _FakePage()
    page.frames[1].text = ""

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    assert "text: <empty>" in next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")


@pytest.mark.unit
def test_a_frame_that_detaches_mid_capture_does_not_lose_the_report(tmp_path: Path) -> None:
    page = _FakePage()
    page.frames[1].text = None

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "text: <unreadable>" in summary
    assert "[unreadable]" in summary
    assert "frame_count:" in summary


@pytest.mark.unit
def test_a_long_frame_text_is_truncated(tmp_path: Path) -> None:
    """The summary is meant to be read; a whole document pasted into it is not."""
    page = _FakePage()
    page.frames[1].text = "x" * 500

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "x" * 160 + "..." in summary
    assert "x" * 200 not in summary


@pytest.mark.unit
def test_the_widget_is_the_frame_that_says_something(tmp_path: Path) -> None:
    """Vendor domains and ids change; a challenge still has to tell a person what to do."""
    page = _FakePage(loads_after_wait=True)
    page.frames[1].text = "Palaikykite nuspaude"

    record_challenge(tmp_path)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "challenge_widget: https://captcha.px-cloud.net/challenge" in summary
    assert "challenge_prompt: Palaikykite nuspaude" in summary


@pytest.mark.unit
def test_a_refusal_with_no_widget_is_reported_as_such(tmp_path: Path) -> None:
    """Nothing to solve means waiting is the only way through, and the file should say so."""
    page = _FakePage()
    page.frames[1].text = ""

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    summary = next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
    assert "challenge_widget: none found" in summary
    assert "challenge_prompt: none" in summary


@pytest.mark.unit
def test_a_blank_silent_frame_is_labelled_a_probe(tmp_path: Path) -> None:
    """about:blank frames that show nothing are environment probes, not failed loads."""
    page = _FakePage()
    page.frames[1].text = ""

    record_challenge(tmp_path, settle_seconds=0.0)(page)

    assert "[probe]" in next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")


@pytest.mark.unit
def test_a_loaded_but_silent_frame_is_labelled_script(tmp_path: Path) -> None:
    page = _FakePage(loads_after_wait=True)
    page.frames[1].text = ""

    record_challenge(tmp_path)(page)

    assert "[script]" in next(tmp_path.glob("*.txt")).read_text(encoding="utf-8")
