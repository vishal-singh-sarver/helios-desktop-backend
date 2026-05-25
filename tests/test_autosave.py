"""Tests for scenario-level autosave + rotation.

Phase 1 dropped project-level autosave (the data was effectively ephemeral
beyond restart anyway). Only `trigger_scenario_autosave` writes to disk
now — to the new nested location
    data/projects/<pid>/scenarios/<sid>/context_file/context.xml
"""
import gzip
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.helios.persistence import trigger_scenario_autosave
from app.core.config import settings


@pytest.fixture
def temp_data_dir():
    """Temp directory standing in for `settings.data_dir`. Patched so all
    `settings.scenario_dir(...)` / `scenario_context_file_dir(...)` lookups
    land under the tmp path."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch.object(settings, "data_dir", tmp_path):
            with patch.object(settings, "projects_dir", tmp_path / "projects"):
                (tmp_path / "projects").mkdir()
                yield tmp_path


def _make_sctx(project_id: str, scenario_id: str, xml_payload: str = "<helios>data</helios>"):
    """Build a fake ScenarioContext-like object with a mock writeXML."""
    sctx = MagicMock()
    sctx.project_id = project_id
    sctx.scenario_id = scenario_id

    def _mock_write_xml(path):
        with open(path, "w") as f:
            f.write(xml_payload)

    sctx.context = MagicMock()
    sctx.context.writeXML.side_effect = _mock_write_xml
    return sctx


def test_noop_when_no_writexml(temp_data_dir):
    """If the PyHelios context lacks writeXML, autosave is a silent no-op."""
    sctx = MagicMock()
    sctx.project_id = "p1"
    sctx.scenario_id = "s1"
    sctx.context = MagicMock(spec=[])  # no writeXML attr

    trigger_scenario_autosave(sctx)

    scenario_dir = settings.scenario_dir("p1", "s1")
    # Nothing should be created
    assert not scenario_dir.exists()


def test_writes_context_xml(temp_data_dir):
    """A live writeXML triggers a save to <scenario>/context_file/context.xml."""
    sctx = _make_sctx("p1", "s1")

    trigger_scenario_autosave(sctx)

    ctx_xml = settings.scenario_context_file_dir("p1", "s1") / "context.xml"
    assert ctx_xml.exists()
    assert "<helios>data</helios>" in ctx_xml.read_text()


def test_rotation_creates_archive(temp_data_dir):
    """A second save rotates the previous context.xml into archives/."""
    sctx = _make_sctx("p1", "s1")

    trigger_scenario_autosave(sctx)
    trigger_scenario_autosave(sctx)

    archives_dir = settings.scenario_context_file_dir("p1", "s1") / "archives"
    archives = list(archives_dir.glob("autosave_*.xml.gz"))
    assert len(archives) == 1

    # The new context.xml is also present
    ctx_xml = settings.scenario_context_file_dir("p1", "s1") / "context.xml"
    assert ctx_xml.exists()


def test_archive_cap_at_10(temp_data_dir):
    """After 12 saves there should be exactly 10 archives — oldest evicted."""
    sctx = _make_sctx("p1", "s1")

    # Saves 1..12 → 11 rotated into archives (last save doesn't rotate
    # itself; it just writes the new current). With cap 10, oldest is
    # evicted to keep the count at 10.
    for _ in range(12):
        trigger_scenario_autosave(sctx)

    archives_dir = settings.scenario_context_file_dir("p1", "s1") / "archives"
    archives = list(archives_dir.glob("autosave_*.xml.gz"))
    assert len(archives) == 10


def test_archive_content_is_gzipped_xml(temp_data_dir):
    """The rotated archive is a gzip-compressed copy of the previous XML."""
    sctx = _make_sctx("p1", "s1", xml_payload="<a>v1</a>")

    trigger_scenario_autosave(sctx)

    # Change the mock to write different content for the second save
    sctx2 = _make_sctx("p1", "s1", xml_payload="<a>v2</a>")
    trigger_scenario_autosave(sctx2)

    archives_dir = settings.scenario_context_file_dir("p1", "s1") / "archives"
    archives = list(archives_dir.glob("autosave_*.xml.gz"))
    assert len(archives) == 1

    decompressed = gzip.decompress(archives[0].read_bytes()).decode()
    assert "<a>v1</a>" in decompressed  # archived snapshot is the older one
