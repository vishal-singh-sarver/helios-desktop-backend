import os
import gzip
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from app.helios.persistence import trigger_autosave
from app.core.config import settings

@pytest.fixture
def temp_projects_dir():
    """Create a temporary projects directory and point settings to it."""
    old_dir = settings.pyhelios_source_path # dummy placeholder for backup if needed
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        with patch.object(settings, 'data_dir', tmp_path):
            # resolved_projects_dir depends on data_dir by default in Settings
            # But in the code it's often settings.resolved_projects_dir.
            # Let's patch resolved_projects_dir directly to be safe.
            with patch.object(settings, 'projects_dir', tmp_path / "projects"):
                (tmp_path / "projects").mkdir()
                yield tmp_path / "projects"

def test_noop_when_no_writexml(temp_projects_dir):
    """If Context doesn't have writeXML, trigger_autosave should be a no-op."""
    ctx = MagicMock(spec=[]) # No writeXML attribute
    project_id = "test_project"
    
    trigger_autosave(ctx, project_id)
    
    proj_dir = temp_projects_dir / project_id
    assert not proj_dir.exists()

def test_writes_current_gz(temp_projects_dir):
    """If Context has writeXML, trigger_autosave should create current.xml.gz."""
    ctx = MagicMock()
    # Mock writeXML to create a dummy XML file at the path provided
    def mock_write_xml(path):
        with open(path, "w") as f:
            f.write("<helios>data</helios>")
    ctx.writeXML.side_effect = mock_write_xml
    
    project_id = "test_project"
    trigger_autosave(ctx, project_id)
    
    current_gz = temp_projects_dir / project_id / "current.xml.gz"
    assert current_gz.exists()
    
    with gzip.open(current_gz, "rt") as f:
        assert f.read() == "<helios>data</helios>"

def test_rotation_creates_archive(temp_projects_dir):
    """A second save should move the previous current.xml.gz to archives."""
    ctx = MagicMock()
    ctx.writeXML.side_effect = lambda p: open(p, "w").write("data")
    
    project_id = "test_project"
    
    # First save
    trigger_autosave(ctx, project_id)
    # Second save
    trigger_autosave(ctx, project_id)
    
    archives_dir = temp_projects_dir / project_id / "autosave_archives"
    archives = list(archives_dir.glob("autosave_*.xml.gz"))
    assert len(archives) == 1
    
    current_gz = temp_projects_dir / project_id / "current.xml.gz"
    assert current_gz.exists()

def test_cap_at_10(temp_projects_dir):
    """After 11 archives (12 saves), the oldest should be deleted."""
    ctx = MagicMock()
    ctx.writeXML.side_effect = lambda p: open(p, "w").write("data")
    
    project_id = "test_project"
    
    # 12 saves = 1 current.xml.gz + 11 potential archives
    # But rotation happens BEFORE the new save.
    # 1st save: creates current.xml.gz (0 archives)
    # 2nd save: rotates current to archive, creates new current (1 archive)
    # ...
    # 11th save: rotates current to archive (now 10 archives), creates new current
    # 12th save: rotates current to archive. Before adding, it sees 10 archives, deletes oldest, then adds new one (stays at 10 archives).
    
    for _ in range(12):
        trigger_autosave(ctx, project_id)
        
    archives_dir = temp_projects_dir / project_id / "autosave_archives"
    archives = list(archives_dir.glob("autosave_*.xml.gz"))
    assert len(archives) == 10
