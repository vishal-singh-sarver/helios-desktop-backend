"""The autosave must not hold the whole scene in RAM.

It used to write the scene to a temp file, read the ENTIRE file back into
memory, and write it out again — the engine had already written it. The
rotation did the same: the previous snapshot read whole into RAM, plus its
compressed copy. ~400 MB of pointless allocation on a 200 MB scene, measured.

That happens inside /discard, which is exactly when the next project starts
loading, so it was a large part of the peak that aborts the backend on Linux
(the process holds ~1.4 GB for one 1000x1000 ground before any of this).

Now: the temp file is MOVED into place (os.replace), and the rotation is
streamed a megabyte at a time. Output is unchanged; only the memory is.
"""
import gzip
import logging
import time
import os
import tracemalloc
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import settings
from app.helios.persistence import (
    _scenario_archives_dir,
    _scenario_context_xml,
    trigger_scenario_autosave,
)

BIG = 24 * 1024 * 1024          # 24 MB — large enough to dwarf any fixed overhead


@pytest.fixture
def data_dir(tmp_path):
    with patch.object(settings, "data_dir", tmp_path), \
            patch.object(settings, "projects_dir", tmp_path / "projects"):
        (tmp_path / "projects").mkdir()
        yield tmp_path


def _sctx(payload: bytes):
    """A ScenarioContext-alike whose writeXML drops `payload` at the given path."""
    s = MagicMock()
    s.project_id, s.scenario_id = "p1", "s1"
    s.context = MagicMock()
    s.context.writeXML.side_effect = lambda path: Path(path).write_bytes(payload)
    return s


def test_save_does_not_buffer_the_scene_in_memory(data_dir):
    """The whole point: peak allocation must not scale with the scene."""
    payload = b"<helios>" + b"x" * BIG + b"</helios>"

    tracemalloc.start()
    trigger_scenario_autosave(_sctx(payload))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    assert _scenario_context_xml("p1", "s1").read_bytes() == payload
    assert peak < BIG / 4, (
        f"peak allocation {peak / 1048576:.1f} MB for a "
        f"{len(payload) / 1048576:.1f} MB scene — the file is being buffered")


def test_rotation_does_not_buffer_the_previous_snapshot(data_dir):
    """The second save archives the first — streamed, not read whole into RAM."""
    first = b"<helios>" + b"a" * BIG + b"</helios>"
    trigger_scenario_autosave(_sctx(first))

    second = b"<helios>" + b"b" * BIG + b"</helios>"
    tracemalloc.start()
    trigger_scenario_autosave(_sctx(second))
    peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()

    archives = list(_scenario_archives_dir("p1", "s1").glob("autosave_*.xml.gz"))
    assert len(archives) == 1
    assert gzip.decompress(archives[0].read_bytes()) == first, \
        "the archive is not a faithful copy of the previous snapshot"
    assert _scenario_context_xml("p1", "s1").read_bytes() == second
    assert peak < BIG / 4, (
        f"peak {peak / 1048576:.1f} MB while rotating a "
        f"{len(first) / 1048576:.1f} MB snapshot — it is still buffering")


def test_content_round_trips_exactly(data_dir):
    """Byte-for-byte, including bytes that are not valid UTF-8."""
    payload = bytes(range(256)) * 4096
    trigger_scenario_autosave(_sctx(payload))
    assert _scenario_context_xml("p1", "s1").read_bytes() == payload


def test_temp_file_lands_beside_the_target_not_in_tmp(data_dir):
    """os.replace cannot cross filesystems — /tmp is often a separate mount, so
    the temp file must be created in the destination directory."""
    seen = {}

    def _capture(path):
        seen["dir"] = Path(path).parent
        Path(path).write_bytes(b"<helios/>")

    s = _sctx(b"")
    s.context.writeXML.side_effect = _capture
    trigger_scenario_autosave(s)

    assert seen["dir"] == _scenario_context_xml("p1", "s1").parent, (
        f"temp written to {seen['dir']} — a rename from there can fail EXDEV")


def test_a_failed_write_leaves_the_previous_scene_intact(data_dir):
    """Atomicity: os.replace either lands the new file or changes nothing."""
    good = b"<helios>good</helios>"
    trigger_scenario_autosave(_sctx(good))
    final = _scenario_context_xml("p1", "s1")
    assert final.read_bytes() == good

    boom = _sctx(b"")
    boom.context.writeXML.side_effect = RuntimeError("engine exploded")
    trigger_scenario_autosave(boom)          # must not raise

    assert final.exists() and final.read_bytes() == good, \
        "a failed save destroyed the previous scene"


def test_no_temp_files_are_left_behind(data_dir):
    """On success and on failure alike."""
    ctx_dir = _scenario_context_xml("p1", "s1").parent

    trigger_scenario_autosave(_sctx(b"<helios>ok</helios>"))
    assert not list(ctx_dir.glob("context.xml.tmp-*")), "temp left after a good save"

    boom = _sctx(b"")
    boom.context.writeXML.side_effect = RuntimeError("nope")
    trigger_scenario_autosave(boom)
    assert not list(ctx_dir.glob("context.xml.tmp-*")), "temp left after a failed save"

    # And nothing stray in /tmp either.
    assert not list(Path("/tmp").glob("context.xml.tmp-*"))


def test_still_a_noop_without_writexml(data_dir):
    """Unchanged contract: no PyHelios, no files created."""
    s = MagicMock()
    s.project_id, s.scenario_id = "p9", "s9"
    s.context = MagicMock(spec=[])
    trigger_scenario_autosave(s)
    assert not settings.scenario_dir("p9", "s9").exists()


def test_a_killed_backend_does_not_strand_its_temp_forever(data_dir):
    """A SIGKILL mid-writeXML leaves the temp beside context.xml.

    It used to be a NamedTemporaryFile in /tmp, which the OS cleared. Now it
    sits in the project folder, where nothing clears it — and _project_disk_stats
    sums that tree, so a 240 MB corpse also inflates the size shown in the UI.
    """
    ctx_dir = _scenario_context_xml("p1", "s1").parent
    ctx_dir.mkdir(parents=True, exist_ok=True)

    corpse = ctx_dir / "context.xml.tmp-deadbeef.xml"
    corpse.write_bytes(b"<helios>half a scene")
    old = time.time() - (2 * 60 * 60)          # two hours ago
    os.utime(corpse, (old, old))

    trigger_scenario_autosave(_sctx(b"<helios>ok</helios>"))

    assert not corpse.exists(), "a stranded temp from a killed backend was kept"
    assert _scenario_context_xml("p1", "s1").read_bytes() == b"<helios>ok</helios>"


def test_a_temp_another_save_is_still_writing_is_left_alone(data_dir):
    """Age-based on purpose. Two saves for one scenario CAN overlap — a queued
    autosave and a discard's synchronous save both take .read(), and readers
    run concurrently — so a fresh temp may belong to a live write."""
    ctx_dir = _scenario_context_xml("p1", "s1").parent
    ctx_dir.mkdir(parents=True, exist_ok=True)

    in_flight = ctx_dir / "context.xml.tmp-inflight.xml"
    in_flight.write_bytes(b"<helios>being written right now")

    trigger_scenario_autosave(_sctx(b"<helios>ok</helios>"))

    assert in_flight.exists(), \
        "deleted a temp another save was still writing"
    in_flight.unlink()


def test_an_unwritable_data_dir_is_logged_not_swallowed(data_dir, caplog):
    """The save must never fail silently.

    mkstemp moved from /tmp into the data directory (required, so os.replace
    cannot fail EXDEV) — which put an unguarded OSError on the path. In the
    deferred path that exception lands on a Future nobody reads, so
    concurrent.futures stores it and says nothing: no stderr, no backend.log,
    and wait_for_scenario_saves() still reports success. EACCES, EROFS, EDQUOT
    and a full disk all reach it.
    """
    ctx_dir = _scenario_context_xml("p1", "s1").parent
    ctx_dir.mkdir(parents=True, exist_ok=True)
    ctx_dir.chmod(0o555)                      # read-only, dirs already exist
    try:
        with caplog.at_level(logging.ERROR):
            trigger_scenario_autosave(_sctx(b"<helios>ok</helios>"))   # must not raise
        assert any("data directory" in r.message or "temp file" in r.message
                   for r in caplog.records), \
            "an unwritable data dir produced no log line at all"
    finally:
        ctx_dir.chmod(0o755)
