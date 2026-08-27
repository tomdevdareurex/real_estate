"""Strict YAML run configuration shared by the offline and live commands."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from aruodas_scraper.constants import (
    MAX_CONFIG_BYTES,
    MAX_DETAIL_FETCHES_PER_CATEGORY,
    MAX_SEARCH_PAGES,
)
from aruodas_scraper.exceptions import ConfigurationError

PropertyTypeOption = Literal["apartments", "houses", "all"]
DealTypeOption = Literal["sale", "rent", "all"]
TransportOption = Literal["curl", "httpx"]
_CITY_PATTERN = r"^[a-z][a-z0-9_-]*$"
# A profile name is interpolated into a filename, so it may not carry a separator or a `..`.
_PROFILE_PATTERN = r"^[a-z][a-z0-9_-]*$"
_SECTION_NAMES = ("scrape_live", "parse_offline")


class ScrapeLiveOptions(BaseModel):
    """Live retrieval options; unset values fall through to CLI defaults."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool | None = None
    cities_config: Path | None = None
    city: str | None = Field(default=None, pattern=_CITY_PATTERN)
    property_type: PropertyTypeOption | None = None
    deal_type: DealTypeOption | None = None
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
    # Element to press and hold on the page a mint lands on, once the origin is already
    # satisfied and the window is still open. Any Playwright selector: `#id`, `.class`,
    # `text=...`, or `xpath=/html/...`. Unset means the mint does nothing but harvest.
    # Where to write a screenshot and a frame summary each time a challenge is raised.
    # Read-only: nothing is clicked. Unset means no capture. Keep it out of the repository.
    challenge_evidence: Path | None = None
    mint_hold_selector: str | None = Field(default=None, min_length=1)
    # How long that press lasts. Only read when mint_hold_selector is set.
    mint_hold_seconds: float | None = Field(default=None, ge=0.0, le=60.0)
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
    # Overlay merged over this file: `profile: pages` also reads `scrape.pages.yaml` beside it,
    # and whatever that sets wins here. It exists so switching what a run does is one word rather
    # than three settings that have to agree - see `load_run_config`.
    profile: str | None = Field(default=None, pattern=_PROFILE_PATTERN)
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


def _read_mapping(path: Path) -> dict[str, Any]:
    """Read one configuration file into a mapping, or say why it could not be read."""
    try:
        if path.stat().st_size > MAX_CONFIG_BYTES:
            raise ConfigurationError(f"Run configuration exceeds {MAX_CONFIG_BYTES} bytes: {path}")
        raw_data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise ConfigurationError(f"Run configuration file was not found: {path}") from error
    except (OSError, UnicodeError) as error:
        raise ConfigurationError(f"Run configuration could not be read: {path}") from error
    except yaml.YAMLError as error:
        raise ConfigurationError(f"Run configuration contains invalid YAML: {path}") from error
    if not isinstance(raw_data, dict):
        raise ConfigurationError("Run configuration must contain a YAML mapping.")
    return raw_data


def _overlay_path(base: Path, profile: str) -> Path:
    """Return the overlay file for a profile, which always sits beside the base file.

    Same-directory is not a convention but a requirement: `_resolve_paths` resolves relative
    paths against the configuration file's directory, so an overlay elsewhere would give the
    merged result two different path bases.
    """
    return base.with_name(f"{base.stem}.{profile}{base.suffix}")


def _available_profiles(base: Path) -> list[str]:
    """Name the overlays that exist beside a base file, for an error worth reading."""
    prefix = f"{base.stem}."
    try:
        candidates = sorted(base.parent.glob(f"{prefix}*{base.suffix}"))
    except OSError:  # pragma: no cover - an unreadable directory is not worth failing over
        return []
    return [candidate.stem[len(prefix) :] for candidate in candidates]


def _merged_sections(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Layer an overlay's sections over the base, key by key within each section."""
    merged = dict(base)
    for name, section in overlay.items():
        current = merged.get(name)
        merged[name] = (
            {**current, **section}
            if isinstance(current, dict) and isinstance(section, dict)
            else section
        )
    return merged


def _selected_profile(raw_data: dict[str, Any], profile: str | None) -> str | None:
    """Decide which profile applies: an explicit argument beats the file's own key."""
    selected = profile if profile is not None else raw_data.get("profile")
    if selected is None:
        return None
    if not isinstance(selected, str):
        raise ConfigurationError(
            f"Profile name must be a string, not {type(selected).__name__}: {selected!r}"
        )
    if re.fullmatch(_PROFILE_PATTERN, selected) is None:
        raise ConfigurationError(
            "Profile name must be lowercase letters or digits, optionally with '-' or '_': "
            f"{selected!r}"
        )
    return selected


def _apply_profile(path: Path, raw_data: dict[str, Any], profile: str) -> dict[str, Any]:
    """Merge the named overlay over an already-read base mapping."""
    overlay_path = _overlay_path(path, profile)
    if not overlay_path.is_file():
        available = ", ".join(_available_profiles(path)) or "none"
        raise ConfigurationError(
            f"Profile {profile!r} was not found at {overlay_path}. Available: {available}"
        )
    overlay = _read_mapping(overlay_path)
    unexpected = sorted(str(name) for name in set(overlay) - set(_SECTION_NAMES))
    if unexpected:
        raise ConfigurationError(
            f"A profile overlay may only set {' or '.join(_SECTION_NAMES)}, so overlays do not "
            f"chain; {overlay_path} also sets: {', '.join(unexpected)}"
        )
    merged = _merged_sections(raw_data, overlay)
    # Record what actually applied, not what the file asked for, so `--profile` is visible
    # downstream even when it overrode the file's own key.
    merged["profile"] = profile
    return merged


def load_run_config(path: Path, profile: str | None = None) -> RunConfig:
    """Load and validate a run configuration file, merging a profile overlay when one applies.

    Precedence is overlay over base file, and `profile` over the file's own `profile` key.
    Relative paths are resolved against the base configuration file's directory.

    Args:
        path: YAML file containing the run configuration.
        profile: Overlay to merge over it, read from `<stem>.<profile><suffix>` beside `path`.
            Overrides any `profile` key in the file; None leaves that key in charge.

    Returns:
        Validated immutable run configuration.

    Raises:
        ConfigurationError: If either file cannot be read or the merged result fails validation.
    """
    raw_data = _read_mapping(path)
    selected = _selected_profile(raw_data, profile)
    if selected is not None:
        raw_data = _apply_profile(path, raw_data, selected)

    try:
        parsed = RunConfig.model_validate(raw_data)
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
    "DealTypeOption",
    "ParseOfflineOptions",
    "PropertyTypeOption",
    "RunConfig",
    "ScrapeLiveOptions",
    "TransportOption",
    "load_run_config",
]
