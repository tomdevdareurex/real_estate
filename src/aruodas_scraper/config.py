"""Strict YAML configuration loading for offline Aruodas processing."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from aruodas_scraper.constants import MAX_CONFIG_BYTES
from aruodas_scraper.exceptions import ConfigurationError


class _SettingsInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    source: Literal["aruodas"]
    input_directory: str
    city: str = Field(min_length=1, pattern=r"^[a-z][a-z0-9_-]*$")
    property_types: tuple[Literal["apartments", "houses"], ...] = Field(min_length=1)

    @field_validator("input_directory")
    @classmethod
    def require_local_directory(cls, value: str) -> str:
        """Require a local input directory path."""
        if "://" in value or value.startswith("//"):
            raise ValueError("input_directory must be a local directory")
        return value


class Settings(BaseModel):
    """Validated runtime settings with an absolute input directory."""

    model_config = ConfigDict(frozen=True)

    schema_version: Literal[1]
    source: Literal["aruodas"]
    input_directory: Path
    city: str
    property_types: tuple[Literal["apartments", "houses"], ...]


def load_settings(path: Path) -> Settings:
    """Load and validate an offline YAML configuration file.

    Args:
        path: Path to the YAML configuration file.

    Returns:
        Validated immutable settings.

    Raises:
        ConfigurationError: If the file cannot be read or is invalid.
    """
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigurationError(f"Configuration exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        raw_content = path.read_text(encoding="utf-8")
        raw_data = yaml.safe_load(raw_content)
        if not isinstance(raw_data, dict):
            raise ConfigurationError("Configuration must contain a YAML mapping.")
        parsed = _SettingsInput.model_validate(raw_data)
    except FileNotFoundError as error:
        raise ConfigurationError(f"Configuration file was not found: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Configuration contains invalid YAML: {path}") from error
    except ValidationError as error:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise ConfigurationError(f"Invalid configuration: {details}") from error

    input_directory = (path.parent / parsed.input_directory).resolve()
    return Settings(
        **parsed.model_dump(exclude={"input_directory"}),
        input_directory=input_directory,
    )


__all__ = ["ConfigurationError", "Settings", "load_settings"]
