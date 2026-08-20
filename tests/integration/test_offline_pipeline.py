from pathlib import Path
from unittest.mock import patch

import pytest

from aruodas_scraper.config import load_settings
from aruodas_scraper.pipelines.offline import OfflinePipeline, SnapshotError


@pytest.mark.integration
def test_pipeline_reads_local_snapshot_without_network_access() -> None:
    settings = load_settings(Path("tests/fixtures/config/offline.yaml"))
    pipeline = OfflinePipeline(settings)

    with (
        patch("socket.socket.connect", side_effect=AssertionError("network access attempted")),
        patch("urllib.request.urlopen", side_effect=AssertionError("network access attempted")),
    ):
        snapshots = pipeline.load_snapshots()

    assert [snapshot.name for snapshot in snapshots] == ["listing.html"]
    assert b"Saved test listing" in snapshots[0].content


@pytest.mark.integration
def test_pipeline_reports_missing_input_directory(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.yaml"
    config_path.write_text(
        "schema_version: 1\nsource: aruodas\n"
        "input_directory: missing\n"
        "city: vilnius\nproperty_types: [apartments]\n",
        encoding="utf-8",
    )
    pipeline = OfflinePipeline(load_settings(config_path))

    with pytest.raises(SnapshotError, match="does not exist"):
        pipeline.load_snapshots()
