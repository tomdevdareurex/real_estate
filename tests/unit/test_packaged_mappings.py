import os
from pathlib import Path

import pytest

from aruodas_scraper.normalization.translations import load_field_mappings


@pytest.mark.unit
def test_default_mappings_load_outside_repository_root(tmp_path: Path) -> None:
    original = Path.cwd()
    os.chdir(tmp_path)
    try:
        mappings = load_field_mappings()
    finally:
        os.chdir(original)

    assert mappings.fields["Plotas"] == "total_area_sqm"
