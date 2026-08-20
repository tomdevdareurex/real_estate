import os
from pathlib import Path

import pytest

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.networking.cookie_source import (
    STALE_AFTER_SECONDS,
    load_cookie_file,
)

_HEADER = "_px3=borrowed; PHPSESSID=abc"


def _write(path: Path, content: str, age_seconds: float = 0.0) -> Path:
    path.write_text(content, encoding="utf-8")
    if age_seconds:
        mtime = os.stat(path).st_mtime - age_seconds
        os.utime(path, (mtime, mtime))
    return path


@pytest.mark.unit
def test_cookie_file_is_read_and_stripped(tmp_path: Path) -> None:
    cookie = load_cookie_file(_write(tmp_path / "cookie.txt", f"  {_HEADER}\n"))

    assert cookie is not None
    assert cookie.header == _HEADER


@pytest.mark.unit
def test_no_cookie_file_means_no_cookie() -> None:
    assert load_cookie_file(None) is None


@pytest.mark.unit
def test_cookie_file_problems_are_reported_as_configuration_errors(tmp_path: Path) -> None:
    empty = _write(tmp_path / "empty.txt", "   \n")

    with pytest.raises(ConfigurationError, match="is empty"):
        load_cookie_file(empty)
    with pytest.raises(ConfigurationError, match="was not found"):
        load_cookie_file(tmp_path / "missing.txt")


@pytest.mark.unit
def test_oversized_cookie_file_is_rejected_without_reading_it(tmp_path: Path) -> None:
    path = _write(tmp_path / "huge.txt", "x" * 20000)

    with pytest.raises(ConfigurationError, match="exceeds"):
        load_cookie_file(path)


@pytest.mark.unit
def test_a_cookie_without_the_protection_token_is_recognised_as_worthless(tmp_path: Path) -> None:
    # Without _px3 the header is an ordinary Aruodas session: it costs a request budget to
    # discover that, so it has to be visible up front.
    cookie = load_cookie_file(_write(tmp_path / "cookie.txt", "PHPSESSID=abc; other=1"))

    assert cookie is not None
    assert cookie.has_protection_token is False
    assert "NO _px3" in cookie.describe()


@pytest.mark.unit
def test_the_protection_token_is_matched_by_name_not_substring(tmp_path: Path) -> None:
    # "_px3" appearing inside another cookie's value must not read as the token itself.
    cookie = load_cookie_file(_write(tmp_path / "cookie.txt", "other=contains_px3_here"))

    assert cookie is not None
    assert cookie.has_protection_token is False


@pytest.mark.unit
def test_a_freshly_copied_cookie_is_not_stale(tmp_path: Path) -> None:
    cookie = load_cookie_file(_write(tmp_path / "cookie.txt", _HEADER))

    assert cookie is not None
    assert cookie.is_stale is False
    assert "likely expired" not in cookie.describe()


@pytest.mark.unit
def test_an_old_cookie_file_is_reported_as_stale(tmp_path: Path) -> None:
    path = _write(tmp_path / "cookie.txt", _HEADER, age_seconds=STALE_AFTER_SECONDS + 600)

    cookie = load_cookie_file(path)

    assert cookie is not None
    assert cookie.is_stale is True
    assert "likely expired" in cookie.describe()


@pytest.mark.unit
def test_a_clock_behind_the_file_mtime_does_not_produce_a_negative_age(tmp_path: Path) -> None:
    path = _write(tmp_path / "cookie.txt", _HEADER)

    cookie = load_cookie_file(path, now=os.stat(path).st_mtime - 5000)

    assert cookie is not None
    assert cookie.age_seconds == 0.0


@pytest.mark.unit
def test_describe_never_discloses_the_cookie_value(tmp_path: Path) -> None:
    cookie = load_cookie_file(_write(tmp_path / "cookie.txt", _HEADER))

    assert cookie is not None
    assert "borrowed" not in cookie.describe()
    assert "abc" not in cookie.describe()
