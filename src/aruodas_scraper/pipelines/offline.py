"""Local HTML snapshot loading."""

from dataclasses import dataclass

from aruodas_scraper.config import Settings
from aruodas_scraper.exceptions import SnapshotError


@dataclass(frozen=True, slots=True)
class HtmlSnapshot:
    """Immutable local HTML input."""

    name: str
    content: bytes


class OfflinePipeline:
    """Load HTML snapshots from a local directory."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    def load_snapshots(self) -> tuple[HtmlSnapshot, ...]:
        """Read all local HTML files in deterministic name order.

        Returns:
            Immutable HTML snapshots.

        Raises:
            SnapshotError: If the input directory does not exist or has no HTML files.
        """
        input_directory = self._settings.input_directory
        if not input_directory.is_dir():
            raise SnapshotError(f"Input directory does not exist: {input_directory}")

        html_paths = sorted(
            path for path in input_directory.iterdir() if path.is_file() and path.suffix == ".html"
        )
        if not html_paths:
            raise SnapshotError(f"Input directory contains no .html files: {input_directory}")

        return tuple(HtmlSnapshot(path.name, path.read_bytes()) for path in html_paths)


__all__ = ["HtmlSnapshot", "OfflinePipeline", "SnapshotError"]
