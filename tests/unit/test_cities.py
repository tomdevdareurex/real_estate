from pathlib import Path

import pytest
from pydantic import ValidationError

from aruodas_scraper.cities import load_city_registry
from aruodas_scraper.exceptions import ConfigurationError


def _write_registry(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "cities.yaml"
    path.write_text(content.lstrip(), encoding="utf-8")
    return path


@pytest.mark.unit
def test_load_city_registry_accepts_repository_configuration() -> None:
    registry = load_city_registry(Path("config/cities.yaml"))

    apartments = registry.get_category("vilnius", "apartments")

    assert apartments.search_url == "https://www.aruodas.lt/butai/vilniuje/"
    assert apartments.listing_id_prefix == "1-"
    assert apartments.output_filename == "apartments_vilnius.csv"


@pytest.mark.unit
def test_city_registry_is_immutable() -> None:
    registry = load_city_registry(Path("config/cities.yaml"))

    with pytest.raises(ValidationError):
        registry.cities["vilnius"].display_name = "Changed"


@pytest.mark.unit
def test_city_registry_rejects_unknown_city_and_category() -> None:
    registry = load_city_registry(Path("config/cities.yaml"))

    with pytest.raises(ConfigurationError, match="Unknown city 'kaunas'"):
        registry.get_city("kaunas")
    with pytest.raises(ConfigurationError, match="Unknown category 'commercial'"):
        registry.get_category("vilnius", "commercial")


@pytest.mark.unit
def test_load_city_registry_rejects_unknown_fields(tmp_path: Path) -> None:
    path = _write_registry(
        tmp_path,
        """
        cities:
          vilnius:
            display_name: Vilnius
            country: Lithuania
            unsupported: true
            categories:
              apartments:
                search_url: https://www.aruodas.lt/butai/vilniuje/
                listing_id_prefix: "1-"
                output_filename: apartments_vilnius.csv
        """,
    )

    with pytest.raises(ConfigurationError, match="unsupported"):
        load_city_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "search_url",
    (
        "http://www.aruodas.lt/butai/vilniuje/",
        "https://example.com/butai/vilniuje/",
        "not-a-url",
    ),
)
def test_load_city_registry_rejects_unsafe_search_url(tmp_path: Path, search_url: str) -> None:
    path = _write_registry(
        tmp_path,
        f"""
        cities:
          vilnius:
            display_name: Vilnius
            country: Lithuania
            categories:
              apartments:
                search_url: {search_url}
                listing_id_prefix: "1-"
                output_filename: apartments_vilnius.csv
        """,
    )

    with pytest.raises(ConfigurationError, match="search_url"):
        load_city_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "output_filename",
    ("../apartments.csv", "apartments.json", "subdir/apartments.csv"),
)
def test_load_city_registry_rejects_unsafe_output_filename(
    tmp_path: Path, output_filename: str
) -> None:
    path = _write_registry(
        tmp_path,
        f"""
        cities:
          vilnius:
            display_name: Vilnius
            country: Lithuania
            categories:
              apartments:
                search_url: https://www.aruodas.lt/butai/vilniuje/
                listing_id_prefix: "1-"
                output_filename: {output_filename}
        """,
    )

    with pytest.raises(ConfigurationError, match="output_filename"):
        load_city_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("category", "listing_id_prefix"),
    (("apartments", "2-"), ("houses", "1-")),
)
def test_load_city_registry_rejects_wrong_listing_prefix(
    tmp_path: Path, category: str, listing_id_prefix: str
) -> None:
    path = _write_registry(
        tmp_path,
        f"""
        cities:
          vilnius:
            display_name: Vilnius
            country: Lithuania
            categories:
              {category}:
                search_url: https://www.aruodas.lt/{category}/vilniuje/
                listing_id_prefix: "{listing_id_prefix}"
                output_filename: {category}_vilnius.csv
        """,
    )

    with pytest.raises(ConfigurationError, match="listing_id_prefix"):
        load_city_registry(path)


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    (
        "cities: {}\n",
        "cities:\n  'Vilnius City': {}\n",
        "cities:\n  vilnius:\n    display_name: Vilnius\n    country: Lithuania\n"
        "    categories:\n      commercial: {}\n",
    ),
)
def test_load_city_registry_rejects_invalid_registry_shape(tmp_path: Path, content: str) -> None:
    path = _write_registry(tmp_path, content)

    with pytest.raises(ConfigurationError):
        load_city_registry(path)
