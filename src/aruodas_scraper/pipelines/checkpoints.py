"""Atomic JSON checkpoints for resumable offline runs."""

import json
import os
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

from aruodas_scraper.exceptions import CheckpointError


@dataclass(frozen=True, slots=True)
class CheckpointContext:
    """Identity of inputs and output partition covered by a checkpoint."""

    input_root: str
    output_root: str
    city: str
    property_type: str


def load_checkpoint(path: Path, context: CheckpointContext) -> dict[str, str]:
    """Load content digests after validating the checkpoint context."""
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as error:
        raise CheckpointError(f"Checkpoint is unreadable or invalid: {path}") from error
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise CheckpointError(f"Checkpoint has an unsupported schema: {path}")
    if data.get("context") != asdict(context):
        raise CheckpointError(
            "Checkpoint belongs to a different input, output, city, or property type."
        )
    processed = data.get("processed_files")
    if not isinstance(processed, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in processed.items()
    ):
        raise CheckpointError(f"Checkpoint processed_files is invalid: {path}")
    return dict(processed)


def save_checkpoint(
    path: Path, context: CheckpointContext, processed_files: dict[str, str]
) -> None:
    """Atomically persist processed file digests after successful exports."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    content = (
        json.dumps(
            {
                "schema_version": 1,
                "context": asdict(context),
                "processed_files": dict(sorted(processed_files.items())),
            },
            indent=2,
        )
        + "\n"
    )
    with temporary.open("x", encoding="utf-8") as file_handle:
        file_handle.write(content)
    os.replace(temporary, path)
