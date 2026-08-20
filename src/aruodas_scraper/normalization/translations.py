"""YAML-driven Lithuanian field and categorical value mappings."""

from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from aruodas_scraper.exceptions import ConfigurationError
from aruodas_scraper.models import ListingRecord


class _MappingSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fields: dict[str, str]
    categories: dict[str, dict[str, str]]
    features: dict[str, str]


@dataclass(frozen=True, slots=True)
class FieldMappings:
    """Immutable mapping tables loaded from YAML."""

    fields: dict[str, str]
    categories: dict[str, dict[str, str]]
    features: dict[str, str]

    def normalize_category(self, field: str, value: str) -> str:
        """Return a stable English category or preserve the Lithuanian value."""
        return self.categories.get(field, {}).get(value, value)


def load_field_mappings(path: Path | None = None) -> FieldMappings:
    """Load field mappings from a YAML file."""
    editable_path = Path("config/field_mappings_lt_en.yaml")
    effective_path = path or (editable_path if editable_path.exists() else None)
    try:
        content = (
            effective_path.read_text(encoding="utf-8")
            if effective_path is not None
            else files("aruodas_scraper.resources")
            .joinpath("field_mappings_lt_en.yaml")
            .read_text(encoding="utf-8")
        )
        schema = _MappingSchema.model_validate(yaml.safe_load(content))
    except (
        FileNotFoundError,
        ModuleNotFoundError,
        TypeError,
        ValidationError,
        yaml.YAMLError,
    ) as error:
        source = str(effective_path) if effective_path is not None else "packaged field mappings"
        raise ConfigurationError(f"Unable to load field mappings: {source}") from error
    derived_targets = {"plot_area"}
    valid_targets = set(ListingRecord.model_fields).union(derived_targets)
    invalid_targets = sorted(set(schema.fields.values()).difference(valid_targets))
    if invalid_targets:
        raise ConfigurationError(
            f"Field mappings target unknown model fields: {', '.join(invalid_targets)}"
        )
    return FieldMappings(
        fields=dict(schema.fields),
        categories={key: dict(value) for key, value in schema.categories.items()},
        features=dict(schema.features),
    )
