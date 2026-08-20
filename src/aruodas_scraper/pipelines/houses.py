"""House-only offline pipeline wrapper."""

from pathlib import Path

from aruodas_scraper.models import ScrapeSummary
from aruodas_scraper.pipelines.all_properties import process_offline


def process_houses(
    input_directory: Path, output_directory: Path, city: str, checkpoint_path: Path
) -> ScrapeSummary:
    """Process only saved house detail pages."""
    return process_offline(input_directory, output_directory, city, "houses", checkpoint_path)
