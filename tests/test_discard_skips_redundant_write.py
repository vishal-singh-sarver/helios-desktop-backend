"""/discard must not re-serialise a scene that is already on disk.

Going back to the project list ran an unconditional writeXML. Every mutation
already queues a save, so that write was almost always byte-identical to the
file already there — ~16s on a high-resolution textured ground, paid while the
user waits for the project list to appear.

Discard now drains the queue and writes only if something actually changed.
Measured on a 700x700 ground: 8.60s -> 0.00s when clean, and still 8.60s when a
mutation raced the save, which is the case that must never be skipped.
"""
from unittest.mock import MagicMock, patch

import pytest

from app.core.scenario_context import ScenarioContext
from app.core.session_store import registry
from app.helios import persistence
from app.services import scenario_service as svc

SESSION, PROJECT = "sess-discard", "proj-discard"


def _register(scenario_id: str) -> ScenarioContext:
    """A live scenario whose writeXML is counted rather than performed."""
    sctx = ScenarioContext(PROJECT, scenario_id)
    sctx.context = MagicMock()
    sctx.context.writeXML.side_effect = lambda p: open(p, "w").write("<helios/>")
    sctx.initialized = sctx.hydrated = True
    registry._scenarios.setdefault(SESSION, {}).setdefault(PROJECT, {})[scenario_id] = sctx
    return sctx


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    with patch.object(persistence.settings, "data_dir", tmp_path), \
            patch.object(persistence.settings, "projects_dir", tmp_path / "projects"):
        (tmp_path / "projects").mkdir(exist_ok=True)
        yield
    registry._scenarios.pop(SESSION, None)


def test_a_clean_scene_is_not_written_again():
    """THE fix. The queued save already put this exact scene on disk."""
    sctx = _register("clean")
    persistence.queue_scenario_autosave(sctx)
    persistence.wait_for_scenario_saves()
    assert sctx.mutation_seq == sctx.saved_seq, "the queued save did not settle"

    before = sctx.context.writeXML.call_count
    result = svc.discard_scenario(SESSION, PROJECT, "clean")

    assert sctx.context.writeXML.call_count == before, \
        "discard re-serialised a scene already on disk"
    assert result["saved"] is False
    assert result["discarded"] is True


def test_a_mutation_that_raced_the_save_is_still_written():
    """The case that must NEVER be skipped — this is why it is a counter."""
    sctx = _register("raced")
    persistence.queue_scenario_autosave(sctx)
    persistence.wait_for_scenario_saves()

    sctx.mutation_seq += 1              # a change the save did not include
    before = sctx.context.writeXML.call_count
    result = svc.discard_scenario(SESSION, PROJECT, "raced")

    assert sctx.context.writeXML.call_count == before + 1, \
        "a change that missed the save was silently dropped"
    assert result["saved"] is True


def test_a_mutation_during_the_write_leaves_the_scene_dirty():
    """Why a bool would be wrong.

    With a bool, a mutation landing mid-writeXML gets cleared by that write
    completing, and never reaches disk. The sequence is captured BEFORE the
    write, so a mutation inside it leaves the two counters unequal.
    """
    sctx = _register("mid-write")

    def _mutate_while_writing(path):
        sctx.mutation_seq += 1          # user edits during the serialise
        open(path, "w").write("<helios/>")

    sctx.context.writeXML.side_effect = _mutate_while_writing
    persistence.trigger_scenario_autosave(sctx)

    assert sctx.mutation_seq != sctx.saved_seq, \
        "a mutation during the write was marked as saved"


def test_a_never_saved_scene_is_written_on_discard():
    """A scenario mutated but never drained must still be persisted."""
    sctx = _register("unsaved")
    sctx.mutation_seq = 1               # mutated, never written

    result = svc.discard_scenario(SESSION, PROJECT, "unsaved")
    assert sctx.context.writeXML.call_count == 1
    assert result["saved"] is True


def test_save_false_still_skips_everything():
    """The cancel path is unchanged: no write, dirty or not."""
    sctx = _register("cancelled")
    sctx.mutation_seq = 1

    result = svc.discard_scenario(SESSION, PROJECT, "cancelled", save=False)
    assert sctx.context.writeXML.call_count == 0
    assert result["saved"] is False


def test_a_failed_write_does_not_mark_the_scene_clean():
    """saved_seq is set after os.replace, so a failure leaves it dirty and the
    next save tries again rather than assuming the file is good."""
    sctx = _register("failed")
    sctx.mutation_seq = 1
    sctx.context.writeXML.side_effect = RuntimeError("engine exploded")

    persistence.trigger_scenario_autosave(sctx)          # must not raise
    assert sctx.saved_seq != sctx.mutation_seq, \
        "a failed write marked the scene as persisted"
