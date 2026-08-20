"""Strict city and property-category configuration loading."""

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from aruodas_scraper.constants import MAX_CONFIG_BYTES
from aruodas_scraper.exceptions import ConfigurationError

PropertyCategory = Literal["apartments", "houses"]

_ALLOWED_HOSTS = frozenset({"aruodas.lt", "www.aruodas.lt"})
_EXPECTED_PREFIXES = {"apartments": "1-", "houses": "2-"}


class CategoryDefinition(BaseModel):
    """Validated retrieval and export settings for one property category."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    search_url: str
    listing_id_prefix: str
    output_filename: str

    @field_validator("search_url")
    @classmethod
    def require_safe_search_url(cls, value: str) -> str:
        """Require an HTTPS URL hosted by Aruodas."""
        try:
            parts = urlsplit(value)
            port = parts.port
        except ValueError as error:
            raise ValueError("search_url must be a valid HTTPS Aruodas URL") from error
        if (
            parts.scheme != "https"
            or parts.hostname not in _ALLOWED_HOSTS
            or parts.username is not None
            or parts.password is not None
            or port not in {None, 443}
            or not parts.path.startswith("/")
        ):
            raise ValueError("search_url must be an HTTPS Aruodas URL")
        return value

    @field_validator("output_filename")
    @classmethod
    def require_safe_csv_filename(cls, value: str) -> str:
        """Require a basename-only CSV output filename."""
        if (
            Path(value).name != value
            or "/" in value
            or "\\" in value
            or not value.endswith(".csv")
            or value in {".csv", "..csv"}
        ):
            raise ValueError("output_filename must be a basename ending in .csv")
        return value


class CityDefinition(BaseModel):
    """Validated city metadata and supported property categories."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    display_name: str = Field(min_length=1)
    country: str = Field(min_length=1)
    categories: dict[PropertyCategory, CategoryDefinition] = Field(min_length=1)

    @model_validator(mode="after")
    def require_category_prefixes(self) -> "CityDefinition":
        """Require listing prefixes to match the parser's category contract."""
        for category, definition in self.categories.items():
            expected = _EXPECTED_PREFIXES[category]
            if definition.listing_id_prefix != expected:
                raise ValueError(f"listing_id_prefix for {category} must be {expected!r}")
        return self


class CityRegistry(BaseModel):
    """Immutable registry of configured cities."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    cities: dict[str, CityDefinition] = Field(min_length=1)

    @field_validator("cities")
    @classmethod
    def require_safe_city_keys(cls, value: dict[str, CityDefinition]) -> dict[str, CityDefinition]:
        """Require stable lowercase keys suitable for paths and CLI arguments."""
        for key in value:
            if (
                not key
                or not key[0].isalpha()
                or not key[0].islower()
                or any(
                    not (character.islower() or character.isdigit() or character in "_-")
                    for character in key
                )
            ):
                raise ValueError(f"Invalid city key: {key!r}")
        return value

    def get_city(self, city: str) -> CityDefinition:
        """Return one city or raise a user-facing configuration error."""
        try:
            return self.cities[city]
        except KeyError as error:
            known = ", ".join(sorted(self.cities))
            raise ConfigurationError(f"Unknown city {city!r}. Known cities: {known}.") from error

    def get_category(self, city: str, category: str) -> CategoryDefinition:
        """Return one configured city category or raise a configuration error."""
        city_definition = self.get_city(city)
        try:
            return city_definition.categories[category]  # type: ignore[index]
        except KeyError as error:
            known = ", ".join(sorted(city_definition.categories))
            raise ConfigurationError(
                f"Unknown category {category!r} for city {city!r}. Known categories: {known}."
            ) from error


def load_city_registry(path: Path) -> CityRegistry:
    """Load and validate city/category retrieval configuration.

    Args:
        path: YAML file containing the city registry.

    Returns:
        Validated immutable city registry.

    Raises:
        ConfigurationError: If the file cannot be read or fails validation.
    """
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigurationError(f"Configuration exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            raise ConfigurationError("City configuration must contain a YAML mapping.")
        return CityRegistry.model_validate(raw_data)
    except FileNotFoundError as error:
        raise ConfigurationError(f"City configuration file was not found: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"City configuration could not be read: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"City configuration contains invalid YAML: {path}") from error
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise ConfigurationError(f"Invalid city configuration: {details}") from error


__all__ = [
    "CategoryDefinition",
    "CityDefinition",
    "CityRegistry",
    "PropertyCategory",
    "load_city_registry",
]
