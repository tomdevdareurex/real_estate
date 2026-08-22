from pathlib import Path

import pytest

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.run_config import load_run_config

VALID = """
schema_version: 1
scrape_live:
  city: vilnius
  property_type: apartments
  max_pages: 3
  max_listings_per_category: 40
  timeout_seconds: 15.0
  min_delay_seconds: 5.0
  jitter_seconds: 2.0
  retry_cooldown_seconds: 120
  max_cooldowns: 2
  max_empty_bursts: 3
  max_runtime_seconds: 3600
  overwrite: true
parse_offline:
  resume: true
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


@pytest.mark.unit
def test_load_run_config_reads_both_command_sections(tmp_path: Path) -> None:
    config = load_run_config(_write(tmp_path / "scrape.yaml", VALID))

    assert config.scrape_live.city == "vilnius"
    assert config.scrape_live.property_type == "apartments"
    assert config.scrape_live.max_pages == 3
    assert config.scrape_live.max_listings_per_category == 40
    assert config.scrape_live.timeout_seconds == 15.0
    assert config.scrape_live.min_delay_seconds == 5.0
    assert config.scrape_live.jitter_seconds == 2.0
    assert config.scrape_live.retry_cooldown_seconds == 120.0
    assert config.scrape_live.max_cooldowns == 2
    assert config.scrape_live.max_empty_bursts == 3
    assert config.scrape_live.max_runtime_seconds == 3600.0
    assert config.scrape_live.overwrite is True
    assert config.parse_offline.resume is True


@pytest.mark.unit
def test_load_run_config_leaves_unset_options_as_none(tmp_path: Path) -> None:
    config = load_run_config(_write(tmp_path / "scrape.yaml", "schema_version: 1\n"))

    assert config.scrape_live.city is None
    assert config.scrape_live.max_pages is None
    assert config.parse_offline.input is None


@pytest.mark.unit
def test_load_run_config_resolves_relative_paths_against_the_config_directory(
    tmp_path: Path,
) -> None:
    content = """
schema_version: 1
scrape_live:
  output: ../exports
  cache: cache
parse_offline:
  input: ../html
"""

    config = load_run_config(_write(tmp_path / "conf" / "scrape.yaml", content))

    assert config.scrape_live.output == (tmp_path / "exports").resolve()
    assert config.scrape_live.cache == (tmp_path / "conf" / "cache").resolve()
    assert config.parse_offline.input == (tmp_path / "html").resolve()


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        "schema_version: 1\nscrape_live:\n  unknown_key: 1\n",
        "schema_version: 1\nscrape_live:\n  max_pages: 0\n",
        "schema_version: 1\nscrape_live:\n  max_pages: 501\n",
        "schema_version: 1\nscrape_live:\n  max_listings_per_category: 20001\n",
        "schema_version: 1\nscrape_live:\n  timeout_seconds: 0.5\n",
        "schema_version: 1\nscrape_live:\n  jitter_seconds: 61\n",
        "schema_version: 1\nscrape_live:\n  jitter_seconds: -1\n",
        "schema_version: 1\nscrape_live:\n  retry_cooldown_seconds: 3601\n",
        "schema_version: 1\nscrape_live:\n  retry_cooldown_seconds: -1\n",
        "schema_version: 1\nscrape_live:\n  max_cooldowns: 21\n",
        "schema_version: 1\nscrape_live:\n  max_cooldowns: -1\n",
        "schema_version: 1\nscrape_live:\n  max_empty_bursts: 0\n",
        "schema_version: 1\nscrape_live:\n  max_empty_bursts: 11\n",
        "schema_version: 1\nscrape_live:\n  max_runtime_seconds: 0\n",
        "schema_version: 1\nscrape_live:\n  property_type: flats\n",
        "schema_version: 1\nscrape_live:\n  city: Vilnius\n",
        "schema_version: 2\n",
        "top_level_unknown: 1\nschema_version: 1\n",
    ],
)
def test_load_run_config_rejects_invalid_configuration(tmp_path: Path, content: str) -> None:
    with pytest.raises(ConfigurationError, match="Invalid run configuration"):
        load_run_config(_write(tmp_path / "scrape.yaml", content))


@pytest.mark.unit
def test_load_run_config_rejects_a_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="was not found"):
        load_run_config(tmp_path / "absent.yaml")


@pytest.mark.unit
def test_load_run_config_rejects_invalid_yaml(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="invalid YAML"):
        load_run_config(_write(tmp_path / "scrape.yaml", "schema_version: [1\n"))


@pytest.mark.unit
def test_load_run_config_rejects_a_non_mapping_document(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="YAML mapping"):
        load_run_config(_write(tmp_path / "scrape.yaml", "- 1\n- 2\n"))


@pytest.mark.unit
@pytest.mark.parametrize("state", [True, False])
def test_load_run_config_rejects_two_commands_in_the_same_state(
    tmp_path: Path, state: bool
) -> None:
    content = (
        f"schema_version: 1\n"
        f"scrape_live:\n  enabled: {str(state).lower()}\n"
        f"parse_offline:\n  enabled: {str(state).lower()}\n"
    )

    with pytest.raises(ConfigurationError, match="exactly one command must be enabled"):
        load_run_config(_write(tmp_path / "scrape.yaml", content))


@pytest.mark.unit
@pytest.mark.parametrize(
    ("content", "expected"),
    [
        (
            "schema_version: 1\nscrape_live:\n  enabled: true\nparse_offline:\n  enabled: false\n",
            "scrape-live",
        ),
        (
            "schema_version: 1\nscrape_live:\n  enabled: false\nparse_offline:\n  enabled: true\n",
            "parse-offline",
        ),
        ("schema_version: 1\nscrape_live:\n  enabled: true\n", "scrape-live"),
        ("schema_version: 1\n", None),
    ],
)
def test_default_command_reflects_the_enabled_section(
    tmp_path: Path, content: str, expected: str | None
) -> None:
    config = load_run_config(_write(tmp_path / "scrape.yaml", content))

    assert config.default_command == expected


@pytest.mark.unit
def test_a_whole_city_traversal_fits_inside_the_configuration_bounds(tmp_path: Path) -> None:
    """The old 20/500 caps stopped a full Vilnius export at roughly one 20-page sweep.

    They were guardrails against a typo, not limits the origin imposes, so they must not be
    what decides how much of a city can be collected.
    """
    path = _write(
        tmp_path / "scrape.yaml",
        "schema_version: 1\nscrape_live:\n  max_pages: 200\n"
        "  max_listings_per_category: 10000\n",
    )

    config = load_run_config(path)

    assert config.scrape_live is not None
    assert config.scrape_live.max_pages == 200
    assert config.scrape_live.max_listings_per_category == 10000
