import os
import shutil
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from aruodas_scraper.cli import app
from aruodas_scraper.models import OnlineScrapeSummary
from aruodas_scraper.networking.cookie_source import STALE_AFTER_SECONDS
from aruodas_scraper.networking.fetcher import PageResponse
from aruodas_scraper.networking.rate_limiter import (
    DEFAULT_JITTER_SECONDS,
    DEFAULT_MINIMUM_DELAY_SECONDS,
    DelayPolicy,
)

runner = CliRunner()


@pytest.mark.integration
def test_parse_offline_command_exports_apartment(tmp_path: Path) -> None:
    input_directory = tmp_path / "input"
    output_directory = tmp_path / "output"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")

    result = runner.invoke(
        app,
        [
            "parse-offline",
            "--input",
            str(input_directory),
            "--property-type",
            "apartments",
            "--output",
            str(output_directory),
        ],
    )

    assert result.exit_code == 0
    assert "Exported 1 apartment" in result.stdout
    assert (output_directory / "apartments_vilnius.csv").exists()


@pytest.mark.integration
def test_validate_command_accepts_generated_csv(tmp_path: Path) -> None:
    csv_path = tmp_path / "apartments_vilnius.csv"
    csv_path.write_text(
        "listing_id,canonical_url,property_type\n"
        "1-1234567,https://www.aruodas.lt/example-1-1234567/,apartment\n",
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(csv_path)])

    assert result.exit_code == 0
    assert "1 valid record" in result.stdout


@pytest.mark.integration
def test_scrape_live_command_runs_bounded_online_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: dict[str, object] = {}

    def fake_process_online(**kwargs: object) -> OnlineScrapeSummary:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return OnlineScrapeSummary(
            city="vilnius",
            started_at_utc=now,
            completed_at_utc=now,
            search_pages_fetched=1,
            listings_discovered=1,
            detail_pages_fetched=1,
            apartments_exported=1,
        )

    monkeypatch.setattr("aruodas_scraper.cli.process_online", fake_process_online)
    output_directory = tmp_path / "output"

    result = runner.invoke(
        app,
        [
            "scrape-live",
            # Without this the run reads the workstation's config/scrape.yaml, whose
            # cookie_file points outside the repository - so the test would pass or fail on
            # whether that file happens to exist, and on how old it is.
            "--no-config",
            "--property-type",
            "apartments",
            "--output",
            str(output_directory),
            "--cache",
            str(tmp_path / "cache"),
            "--max-pages",
            "1",
            "--max-listings-per-category",
            "1",
        ],
    )

    assert result.exit_code == 0
    assert captured["city"] == "vilnius"
    assert captured["property_type"] == "apartments"
    assert captured["max_pages"] == 1
    assert captured["max_listings_per_category"] == 1
    assert "Exported 1 apartment(s) and 0 house(s)" in result.stdout


def _capture_scrape_live(monkeypatch: pytest.MonkeyPatch) -> dict[str, object]:
    captured: dict[str, object] = {}

    def fake_process_online(**kwargs: object) -> OnlineScrapeSummary:
        captured.update(kwargs)
        now = datetime.now(UTC)
        return OnlineScrapeSummary(city="vilnius", started_at_utc=now, completed_at_utc=now)

    monkeypatch.setattr("aruodas_scraper.cli.process_online", fake_process_online)
    return captured


def _write_run_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "scrape.yaml"
    path.write_text(f"schema_version: 1\n{body}", encoding="utf-8")
    return path


@pytest.mark.integration
def test_scrape_live_reads_options_from_the_run_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)
    config = _write_run_config(
        tmp_path,
        "scrape_live:\n"
        "  property_type: houses\n"
        "  max_pages: 4\n"
        "  max_listings_per_category: 7\n"
        "  overwrite: true\n",
    )

    result = runner.invoke(app, ["scrape-live", "--config", str(config)])

    assert result.exit_code == 0
    assert captured["property_type"] == "houses"
    assert captured["max_pages"] == 4
    assert captured["max_listings_per_category"] == 7
    assert captured["overwrite"] is True


@pytest.mark.integration
def test_scrape_live_command_line_options_override_the_run_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)
    config = _write_run_config(tmp_path, "scrape_live:\n  property_type: houses\n  max_pages: 4\n")

    result = runner.invoke(
        app,
        ["scrape-live", "--config", str(config), "--property-type", "apartments"],
    )

    assert result.exit_code == 0
    assert captured["property_type"] == "apartments"
    assert captured["max_pages"] == 4


@pytest.mark.integration
def test_scrape_live_no_config_ignores_the_run_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)
    config = _write_run_config(tmp_path, "scrape_live:\n  max_pages: 4\n")

    result = runner.invoke(
        app,
        ["scrape-live", "--config", str(config), "--no-config", "--max-listings-per-category", "3"],
    )

    assert result.exit_code == 0
    assert captured["max_pages"] == 1
    assert captured["max_listings_per_category"] == 3


@pytest.mark.integration
def test_scrape_live_default_pacing_reaches_the_client_as_a_number(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Regression: the --min-delay-seconds default must be a real number.

    DelayPolicy is a slotted dataclass, so DelayPolicy.minimum_seconds is a member
    descriptor rather than 10.0. Using it as the option default made click raise while
    casting the default to a float, so scrape-live could not start at all.
    """
    _capture_scrape_live(monkeypatch)
    policies: list[DelayPolicy] = []

    class RecordingClient:
        def __init__(self, **kwargs: object) -> None:
            policies.append(kwargs["delay_policy"])  # type: ignore[arg-type]

        def __enter__(self) -> "RecordingClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("aruodas_scraper.cli.AruodasHttpClient", RecordingClient)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    # centred(10, 2) floors at 8 and draws from [0, 4], so the mean is the documented default.
    assert policies[0].minimum_seconds == DEFAULT_MINIMUM_DELAY_SECONDS - DEFAULT_JITTER_SECONDS
    policies[0].validate()
    assert policies[0].random_max_seconds == 2 * DEFAULT_JITTER_SECONDS


@pytest.mark.integration
def test_scrape_live_pacing_options_reach_the_client_as_a_band_with_reading_pauses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_scrape_live(monkeypatch)
    policies: list[DelayPolicy] = []

    class RecordingClient:
        def __init__(self, **kwargs: object) -> None:
            policies.append(kwargs["delay_policy"])  # type: ignore[arg-type]

        def __enter__(self) -> "RecordingClient":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

    monkeypatch.setattr("aruodas_scraper.cli.AruodasHttpClient", RecordingClient)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--min-delay-seconds",
            "5",
            "--jitter-seconds",
            "2",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    policy = policies[0]
    assert (policy.minimum_seconds, policy.random_min_seconds, policy.random_max_seconds) == (
        3.0,
        0.0,
        4.0,
    )
    # The live path also pauses occasionally, so a delay is either an ordinary in-band gap or
    # an in-band gap plus a reading pause. Nothing may fall between or outside the two.
    assert policy.pause_probability > 0
    durations = [policy.wait(lambda _seconds: None) for _ in range(200)]
    assert all(3.0 <= duration <= 7.0 or 33.0 <= duration <= 97.0 for duration in durations)
    assert any(duration > 7.0 for duration in durations)


def _stub_doctor_fetcher(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer both of doctor's probes with a healthy page, so only reporting is exercised."""

    class HealthyFetcher:
        def fetch_page(self, _url: str, _headers: object) -> PageResponse:
            return PageResponse(status_code=200, headers={}, body=b"<html></html>")

        def close(self) -> None:
            return None

    monkeypatch.setattr("aruodas_scraper.cli.build_fetcher", lambda *_a, **_k: HealthyFetcher())


@pytest.mark.integration
def test_doctor_reports_a_usable_cookie(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_doctor_fetcher(monkeypatch)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("_px3=borrowed; PHPSESSID=abc", encoding="utf-8")

    result = runner.invoke(
        app, ["doctor", "--no-config", "--cookie-file", str(cookie_file), "--transport", "httpx"]
    )

    assert result.exit_code == 0
    assert "Cookie:" in result.stdout
    assert "present" in result.stdout
    # The value authenticates this client to the origin and must never be echoed.
    assert "borrowed" not in result.output


@pytest.mark.integration
def test_doctor_warns_when_the_cookie_carries_no_protection_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Today an expired or worthless cookie is indistinguishable from no cookie: both just
    # produce a lower ceiling, hours into a run. Doctor has to say so before that happens.
    _stub_doctor_fetcher(monkeypatch)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("PHPSESSID=abc", encoding="utf-8")

    result = runner.invoke(
        app, ["doctor", "--no-config", "--cookie-file", str(cookie_file), "--transport", "httpx"]
    )

    assert result.exit_code == 0
    assert "NO _px3" in result.output
    assert "will not raise the request ceiling" in result.output


@pytest.mark.integration
def test_doctor_warns_that_an_old_cookie_has_probably_been_rotated(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_doctor_fetcher(monkeypatch)
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("_px3=borrowed", encoding="utf-8")
    stale = os.stat(cookie_file).st_mtime - (STALE_AFTER_SECONDS + 600)
    os.utime(cookie_file, (stale, stale))

    result = runner.invoke(
        app, ["doctor", "--no-config", "--cookie-file", str(cookie_file), "--transport", "httpx"]
    )

    assert result.exit_code == 0
    assert "likely expired" in result.output
    assert "probably been rotated" in result.output


@pytest.mark.integration
def test_doctor_reports_running_without_a_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_doctor_fetcher(monkeypatch)

    result = runner.invoke(app, ["doctor", "--no-config", "--transport", "httpx"])

    assert result.exit_code == 0
    assert "Cookie:     none" in result.stdout


@pytest.mark.integration
def test_scrape_live_rejects_jitter_wider_than_the_minimum_delay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        ["scrape-live", "--no-config", "--min-delay-seconds", "2", "--jitter-seconds", "5"],
    )

    assert result.exit_code != 0
    assert "cannot exceed" in result.output


@pytest.mark.integration
def test_scrape_live_passes_the_retry_cooldown_to_the_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--retry-cooldown-seconds",
            "90",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    assert captured["retry_cooldown_seconds"] == 90.0


@pytest.mark.integration
def test_scrape_live_reads_the_retry_cooldown_from_the_run_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)
    config = _write_run_config(
        tmp_path,
        "scrape_live:\n  min_delay_seconds: 5.0\n  jitter_seconds: 2.0\n"
        "  retry_cooldown_seconds: 120\n",
    )

    result = runner.invoke(app, ["scrape-live", "--config", str(config)])

    assert result.exit_code == 0
    assert captured["retry_cooldown_seconds"] == 120.0


def _stale_cookie(tmp_path: Path) -> Path:
    cookie_file = tmp_path / "cookie.txt"
    cookie_file.write_text("_px3=borrowed", encoding="utf-8")
    stale = os.stat(cookie_file).st_mtime - (STALE_AFTER_SECONDS + 600)
    os.utime(cookie_file, (stale, stale))
    return cookie_file


@pytest.mark.integration
def test_scrape_live_passes_the_empty_burst_limit_to_the_pipeline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--max-empty-bursts",
            "3",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    assert captured["max_empty_bursts"] == 3


@pytest.mark.integration
def test_scrape_live_refuses_to_start_a_waiting_run_on_a_stale_cookie(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run that waits out cooldowns on a rotated cookie holds the request ceiling of no
    # cookie at all, so it spends hours to collect almost nothing. Cheaper to say so first.
    captured = _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--cookie-file",
            str(_stale_cookie(tmp_path)),
            "--transport",
            "httpx",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 1
    assert "Refusing to start" in result.output
    assert captured == {}


@pytest.mark.integration
def test_scrape_live_starts_on_a_stale_cookie_when_it_is_explicitly_allowed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--cookie-file",
            str(_stale_cookie(tmp_path)),
            "--allow-stale-cookie",
            "--transport",
            "httpx",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    assert captured != {}


@pytest.mark.integration
def test_scrape_live_starts_on_a_stale_cookie_when_the_run_never_waits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Without cooldowns there are no hours to waste, so a quick check stays usable.
    captured = _capture_scrape_live(monkeypatch)

    result = runner.invoke(
        app,
        [
            "scrape-live",
            "--no-config",
            "--cookie-file",
            str(_stale_cookie(tmp_path)),
            "--max-cooldowns",
            "0",
            "--transport",
            "httpx",
            "--output",
            str(tmp_path / "output"),
            "--cache",
            str(tmp_path / "cache"),
        ],
    )

    assert result.exit_code == 0
    assert captured != {}


@pytest.mark.integration
def test_scrape_live_reports_a_missing_explicit_run_configuration(tmp_path: Path) -> None:
    result = runner.invoke(app, ["scrape-live", "--config", str(tmp_path / "absent.yaml")])

    assert result.exit_code == 1
    assert "was not found" in result.output


@pytest.mark.integration
def test_parse_offline_reads_the_input_directory_from_the_run_configuration(
    tmp_path: Path,
) -> None:
    input_directory = tmp_path / "input"
    input_directory.mkdir()
    shutil.copy("tests/fixtures/html/apartment_detail.html", input_directory / "apartment.html")
    output_directory = tmp_path / "output"
    config = _write_run_config(
        tmp_path,
        f"parse_offline:\n"
        f"  input: {input_directory.as_posix()}\n"
        f"  output: {output_directory.as_posix()}\n"
        f"  property_type: apartments\n",
    )

    result = runner.invoke(app, ["parse-offline", "--config", str(config)])

    assert result.exit_code == 0
    assert (output_directory / "apartments_vilnius.csv").exists()


@pytest.mark.integration
def test_parse_offline_requires_an_input_directory_when_unconfigured() -> None:
    result = runner.invoke(app, ["parse-offline", "--no-config"])

    assert result.exit_code != 0
    assert "--input is required" in result.output


@pytest.mark.integration
def test_show_config_prints_merged_settings_for_a_command() -> None:
    result = runner.invoke(app, ["show-config", "--command", "scrape-live"])

    assert result.exit_code == 0
    assert "max_pages:" in result.stdout
    assert "overwrite:" in result.stdout


def _write_discovered_config(tmp_path: Path, body: str) -> None:
    config_directory = tmp_path / "config"
    config_directory.mkdir(parents=True, exist_ok=True)
    shutil.copy("config/cities.yaml", config_directory / "cities.yaml")
    (config_directory / "scrape.yaml").write_text(f"schema_version: 1\n{body}", encoding="utf-8")


@pytest.mark.integration
def test_bare_invocation_runs_the_command_enabled_in_the_run_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = _capture_scrape_live(monkeypatch)
    _write_discovered_config(
        tmp_path,
        "scrape_live:\n  enabled: true\n  max_pages: 6\nparse_offline:\n  enabled: false\n",
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "Running scrape-live" in result.stdout
    assert captured["max_pages"] == 6


@pytest.mark.integration
def test_bare_invocation_shows_help_when_no_command_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_discovered_config(tmp_path, "scrape_live:\n  max_pages: 6\n")
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, [])

    assert result.exit_code == 0
    assert "scrape-live" in result.stdout
    assert "parse-offline" in result.stdout


@pytest.mark.integration
def test_bare_invocation_reports_two_commands_in_the_same_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_discovered_config(
        tmp_path, "scrape_live:\n  enabled: true\nparse_offline:\n  enabled: true\n"
    )
    monkeypatch.chdir(tmp_path)

    result = runner.invoke(app, [])

    assert result.exit_code == 1
    assert "exactly one command must be enabled" in result.output
