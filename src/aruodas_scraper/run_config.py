"""Strict YAML run configuration shared by the offline and live commands."""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aruodas_scraper.constants import (
    MAX_CONFIG_BYTES,
    MAX_DETAIL_FETCHES_PER_CATEGORY,
    MAX_SEARCH_PAGES,
)
from aruodas_scraper.exceptions import ConfigurationError

PropertyTypeOption = Literal["apartments", "houses", "all"]
TransportOption = Literal["curl", "httpx"]
_CITY_PATTERN = r"^[a-z][a-z0-9_-]*$"


class ScrapeLiveOptions(BaseModel):
    """Live retrieval options; unset values fall through to CLI defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool | None = None
    cities_config: Path | None = None
    city: str | None = Field(default=None, pattern=_CITY_PATTERN)
    property_type: PropertyTypeOption | None = None
    output: Path | None = None
    cache: Path | None = None
    max_pages: int | None = Field(default=None, ge=1, le=MAX_SEARCH_PAGES)
    max_listings_per_category: int | None = Field(
        default=None, ge=1, le=MAX_DETAIL_FETCHES_PER_CATEGORY
    )
    timeout_seconds: float | None = Field(default=None, ge=1.0, le=120.0)
    # Mean gap between requests; the actual wait is this give or take jitter_seconds.
    min_delay_seconds: float | None = Field(default=None, ge=0.0, le=600.0)
    # Half-width of the symmetric jitter band around min_delay_seconds. The delay is drawn
    # uniformly from [min_delay - jitter, min_delay + jitter], so it may not exceed it.
    jitter_seconds: float | None = Field(default=None, ge=0.0, le=60.0)
    # How long to wait out a block before carrying on. Zero means never wait, which ends the
    # run at the first refusal.
    retry_cooldown_seconds: float | None = Field(default=None, ge=0.0, le=3600.0)
    # How many blocks the run may wait out, and the wall-clock ceiling for the whole run.
    # Together these bound a run that would otherwise sit in cooldowns indefinitely.
    max_cooldowns: int | None = Field(default=None, ge=0, le=20)
    # Consecutive bursts that may serve nothing before the run stops, so a block that is not
    # lapsing costs one cooldown rather than the whole allowance.
    max_empty_bursts: int | None = Field(default=None, ge=1, le=10)
    # How many blocks solve_on_block may clear. Each renewal is worth roughly a burst, so
    # this is what bounds an attended run: the default is sized for a top-up, and a
    # full-city walk needs far more.
    max_session_renewals: int | None = Field(default=None, ge=0, le=1000)
    max_runtime_seconds: float | None = Field(default=None, ge=1.0)
    # Open a browser on a block so the challenge can be solved, instead of waiting the block
    # out. Solving clears it immediately, so this replaces a 25-minute wait with a click.
    # Needs a person at the keyboard, which is why it is off unless asked for.
    solve_on_block: bool | None = None
    # Ask at the start whether to add details to listings already found, or to walk search
    # pages looking for new ones. The two compete for one request budget, so a run does one
    # or the other; asking makes that choice visible instead of implied by `deepen`.
    ask_phase: bool | None = None
    refresh_cache: bool | None = None
    overwrite: bool | None = None
    deepen: bool | None = None
    user_agent: str | None = Field(default=None, min_length=1)
    ca_bundle: Path | None = None
    proxy: str | None = Field(default=None, min_length=1)
    http2: bool | None = None
    transport: TransportOption | None = None
    impersonate: str | None = Field(default=None, min_length=1)
    cookie_file: Path | None = None


class ParseOfflineOptions(BaseModel):
    """Offline parsing options; unset values fall through to CLI defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool | None = None
    input: Path | None = None
    property_type: PropertyTypeOption | None = None
    city: str | None = Field(default=None, pattern=_CITY_PATTERN)
    output: Path | None = None
    checkpoint: Path | None = None
    resume: bool | None = None
    refresh: bool | None = None
    translate: bool | None = None


class RunConfig(BaseModel):
    """Validated run configuration for every supported command."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1]
    scrape_live: ScrapeLiveOptions = ScrapeLiveOptions()
    parse_offline: ParseOfflineOptions = ParseOfflineOptions()

    @model_validator(mode="after")
    def reject_two_commands_in_the_same_state(self) -> RunConfig:
        """Reject configurations where both commands are enabled or both disabled."""
        decided = [
            value
            for value in (self.scrape_live.enabled, self.parse_offline.enabled)
            if value is not None
        ]
        if len(decided) == 2 and len(set(decided)) == 1:
            state = "enabled" if decided[0] else "disabled"
            raise ValueError(
                f"scrape_live and parse_offline are both {state}; "
                "exactly one command must be enabled"
            )
        return self

    @property
    def default_command(self) -> str | None:
        """Command to run when the CLI is invoked without a subcommand."""
        if self.scrape_live.enabled:
            return "scrape-live"
        if self.parse_offline.enabled:
            return "parse-offline"
        return None


def _describe(error: ValidationError) -> str:
    """Render pydantic errors, omitting the empty location of model-level failures."""
    messages = []
    for item in error.errors():
        location = ".".join(str(part) for part in item["loc"])
        messages.append(f"{location}: {item['msg']}" if location else item["msg"])
    return "; ".join(messages)


def _resolve_paths(section: BaseModel, base_directory: Path) -> BaseModel:
    updates = {
        name: (base_directory / value).resolve()
        for name, value in section
        if isinstance(value, Path)
    }
    return section.model_copy(update=updates)


def load_run_config(path: Path) -> RunConfig:
    """Load and validate a run configuration file.

    Relative paths are resolved against the configuration file's directory.

    Args:
        path: YAML file containing the run configuration.

    Returns:
        Validated immutable run configuration.

    Raises:
        ConfigurationError: If the file cannot be read or fails validation.
    """
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigurationError(f"Run configuration exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            raise ConfigurationError("Run configuration must contain a YAML mapping.")
        parsed = RunConfig.model_validate(raw_data)
    except FileNotFoundError as error:
        raise ConfigurationError(f"Run configuration file was not found: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Run configuration could not be read: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Run configuration contains invalid YAML: {path}") from error
    except ValidationError as error:
        raise ConfigurationError(f"Invalid run configuration: {_describe(error)}") from error

    base_directory = path.parent
    return parsed.model_copy(
        update={
            "scrape_live": _resolve_paths(parsed.scrape_live, base_directory),
            "parse_offline": _resolve_paths(parsed.parse_offline, base_directory),
        }
    )


__all__ = [
    "ParseOfflineOptions",
    "PropertyTypeOption",
    "RunConfig",
    "ScrapeLiveOptions",
    "TransportOption",
    "load_run_config",
]
