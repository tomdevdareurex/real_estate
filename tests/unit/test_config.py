from pathlib import Path

import pytest

from aruodas_scraper.config import ConfigurationError, load_settings

FIXTURE_CONFIG = Path("tests/fixtures/config/offline.yaml")


@pytest.mark.unit
def test_load_settings_accepts_local_configuration() -> None:
    settings = load_settings(FIXTURE_CONFIG)

    assert settings.city == "vilnius"
    assert settings.input_directory == Path("tests/fixtures/snapshot_html").resolve()
    assert settings.property_types == ("apartments", "houses")


@pytest.mark.unit
def test_load_settings_rejects_remote_input_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "remote.yaml"
    config_path.write_text(
        "schema_version: 1\nsource: aruodas\n"
        "input_directory: https://www.aruodas.lt/\n"
        "city: vilnius\nproperty_types: [apartments]\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="local directory"):
        load_settings(config_path)


@pytest.mark.unit
def test_load_settings_rejects_unknown_keys(tmp_path: Path) -> None:
    config_path = tmp_path / "unknown.yaml"
    config_path.write_text(
        "schema_version: 1\nsource: aruodas\n"
        "input_directory: html\n"
        "city: vilnius\nproperty_types: [houses]\nstealth: true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="stealth"):
        load_settings(config_path)
