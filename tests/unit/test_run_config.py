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


MINT_HOLD = """
schema_version: 1
scrape_live:
  mint_hold_selector: "#target"
  mint_hold_seconds: 3.5
"""


@pytest.mark.unit
def test_the_mint_hold_is_read_from_the_config(tmp_path: Path) -> None:
    config = load_run_config(_write(tmp_path / "scrape.yaml", MINT_HOLD))

    assert config.scrape_live is not None
    assert config.scrape_live.mint_hold_selector == "#target"
    assert config.scrape_live.mint_hold_seconds == 3.5


@pytest.mark.unit
def test_an_unset_mint_hold_leaves_the_selector_unset(tmp_path: Path) -> None:
    """A mint that holds nothing is the normal case, so the selector may not gain a default."""
    config = load_run_config(_write(tmp_path / "scrape.yaml", VALID))

    assert config.scrape_live is not None
    assert config.scrape_live.mint_hold_selector is None


@pytest.mark.unit
@pytest.mark.parametrize("seconds", ["-1.0", "61.0"])
def test_an_out_of_range_hold_is_rejected(tmp_path: Path, seconds: str) -> None:
    content = "schema_version: 1\nscrape_live:\n  mint_hold_seconds: " + seconds + "\n"

    with pytest.raises(ConfigurationError):
        load_run_config(_write(tmp_path / "scrape.yaml", content))


@pytest.mark.unit
def test_deal_type_is_read_from_the_configuration(tmp_path: Path) -> None:
    path = _write(tmp_path / "scrape.yaml", "schema_version: 1\nscrape_live:\n  deal_type: rent\n")

    assert load_run_config(path).scrape_live.deal_type == "rent"


@pytest.mark.unit
def test_an_unknown_deal_type_is_refused(tmp_path: Path) -> None:
    path = _write(tmp_path / "scrape.yaml", "schema_version: 1\nscrape_live:\n  deal_type: lease\n")

    with pytest.raises(ConfigurationError, match="deal_type"):
        load_run_config(path)


BASE_WITH_PROFILE = """
schema_version: 1
profile: pages
scrape_live:
  city: vilnius
  max_pages: 200
  max_listings_per_category: 40
  deepen: true
  output: ../exports
"""

PAGES_OVERLAY = """
scrape_live:
  deepen: false
  max_listings_per_category: 1
"""


def _write_profile(directory: Path, name: str, content: str) -> None:
    _write(directory / f"scrape.{name}.yaml", content)


@pytest.mark.unit
def test_a_profile_overlay_wins_over_the_base_and_leaves_the_rest_alone(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", BASE_WITH_PROFILE)
    _write_profile(tmp_path, "pages", PAGES_OVERLAY)

    config = load_run_config(base)

    assert config.profile == "pages"
    assert config.scrape_live.deepen is False
    assert config.scrape_live.max_listings_per_category == 1
    # Untouched by the overlay, so the base still decides.
    assert config.scrape_live.city == "vilnius"
    assert config.scrape_live.max_pages == 200


@pytest.mark.unit
def test_an_explicit_profile_argument_beats_the_files_own_key(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", BASE_WITH_PROFILE)
    _write_profile(tmp_path, "pages", PAGES_OVERLAY)
    _write_profile(tmp_path, "pubs", "scrape_live:\n  max_listings_per_category: 10000\n")

    config = load_run_config(base, "pubs")

    assert config.profile == "pubs"
    assert config.scrape_live.max_listings_per_category == 10000
    # The base's own `deepen` stands, because the pubs overlay says nothing about it.
    assert config.scrape_live.deepen is True


@pytest.mark.unit
def test_no_profile_anywhere_leaves_the_base_untouched(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)

    config = load_run_config(base)

    assert config.profile is None
    assert config.scrape_live.max_listings_per_category == 40


@pytest.mark.unit
def test_an_unknown_profile_names_the_ones_that_exist(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)
    _write_profile(tmp_path, "pages", PAGES_OVERLAY)
    _write_profile(tmp_path, "pubs", PAGES_OVERLAY)

    with pytest.raises(ConfigurationError, match="Available: pages, pubs"):
        load_run_config(base, "nope")


@pytest.mark.unit
@pytest.mark.parametrize("name", ["../../etc/passwd", "Pages", "a/b", "", "1pages"])
def test_a_profile_name_that_could_escape_the_config_directory_is_rejected(
    tmp_path: Path, name: str
) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)

    with pytest.raises(ConfigurationError, match="Profile name must be"):
        load_run_config(base, name)


@pytest.mark.unit
def test_an_overlay_may_not_set_anything_but_the_command_sections(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)
    _write_profile(tmp_path, "pages", "profile: pubs\nscrape_live:\n  deepen: false\n")

    with pytest.raises(ConfigurationError, match="overlays do not chain"):
        load_run_config(base, "pages")


@pytest.mark.unit
def test_a_missing_overlay_file_is_reported_even_when_no_profiles_exist(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)

    with pytest.raises(ConfigurationError, match="Available: none"):
        load_run_config(base, "pages")


@pytest.mark.unit
def test_paths_still_resolve_against_the_base_file_when_an_overlay_applies(
    tmp_path: Path,
) -> None:
    base = _write(tmp_path / "conf" / "scrape.yaml", BASE_WITH_PROFILE)
    _write_profile(tmp_path / "conf", "pages", "scrape_live:\n  cache: cache\n")

    config = load_run_config(base)

    assert config.scrape_live.output == (tmp_path / "exports").resolve()
    assert config.scrape_live.cache == (tmp_path / "conf" / "cache").resolve()


@pytest.mark.unit
def test_an_overlay_is_validated_like_the_base(tmp_path: Path) -> None:
    base = _write(tmp_path / "scrape.yaml", VALID)
    _write_profile(tmp_path, "pages", "scrape_live:\n  max_pages: 0\n")

    with pytest.raises(ConfigurationError, match="Invalid run configuration"):
        load_run_config(base, "pages")
