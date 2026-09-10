"""A burst of mutations costs ONE context.xml write, not one per mutation.

Every mutation queued a save immediately, and a save holds sctx.lock.read() for
the whole write — so the next mutation waited for it. Creating a second
1000x1000 ground sat behind the first ground's ~215 MB write before it could
start building, and the scene was serialised twice (215 MB, then 430 MB).

The submit is debounced: a mutation arriving inside the window replaces the
pending save, which therefore never runs. The survivor serialises the LIVE
context, so it contains every coalesced mutation.
"""
import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from app.core.scenario_context import ScenarioContext
from app.helios import persistence

PROJECT = "proj-coalesce"


@pytest.fixture(autouse=True)
def _isolate(tmp_path):
    """Real timers, but a window short enough to test against, and a clean
    module state so one test cannot leave a timer running into the next."""
    with patch.object(persistence.settings, "data_dir", tmp_path), \
            patch.object(persistence.settings, "projects_dir", tmp_path / "projects"), \
            patch.object(persistence, "_DEBOUNCE_SECONDS", 0.20):
        (tmp_path / "projects").mkdir(exist_ok=True)
        yield
    for timer in list(persistence._PENDING_TIMERS.values()):
        timer.cancel()
    persistence._PENDING_TIMERS.clear()
    persistence._PENDING_SCTX.clear()
    persistence._PENDING_FUTURES.clear()


def _sctx(scenario_id: str) -> ScenarioContext:
    sctx = ScenarioContext(PROJECT, scenario_id)
    sctx.context = MagicMock()
    sctx.context.writeXML.side_effect = lambda p: open(p, "w").write("<helios/>")
    sctx.initialized = sctx.hydrated = True
    return sctx


def test_a_burst_of_mutations_writes_once():
    """Five mutations in quick succession -> one write, not five."""
    sctx = _sctx("burst")
    for _ in range(5):
        persistence.queue_scenario_autosave(sctx)

    persistence.wait_for_scenario_saves(sctx)
    assert sctx.context.writeXML.call_count == 1
    # Every mutation still counted, so the scene can never look clean when it
    # is not — the coalesced-away saves must not swallow the dirty flag.
    assert sctx.mutation_seq == 5
    assert sctx.saved_seq == 5


def test_wait_flushes_a_pending_save():
    """THE DANGEROUS ONE. A debounced save has not reached the pool yet, so
    draining the pool alone would return before the write happened — and
    /discard would release the context believing it was on disk."""
    sctx = _sctx("flush")
    persistence.queue_scenario_autosave(sctx)
    assert sctx.context.writeXML.call_count == 0, "should not have written yet"

    started = time.monotonic()
    persistence.wait_for_scenario_saves(sctx)
    # Flushed, not waited out: returning must not take the whole window.
    assert time.monotonic() - started < persistence._DEBOUNCE_SECONDS
    assert sctx.context.writeXML.call_count == 1


def test_wait_without_a_scenario_flushes_every_pending_save():
    """Shutdown and the test suite drain everything."""
    a, b = _sctx("all-a"), _sctx("all-b")
    persistence.queue_scenario_autosave(a)
    persistence.queue_scenario_autosave(b)

    persistence.wait_for_scenario_saves()
    assert a.context.writeXML.call_count == 1
    assert b.context.writeXML.call_count == 1


def test_a_mutation_during_a_running_save_gets_its_own_write():
    """A save already RUNNING cannot be cancelled and may predate the newest
    mutation, so that mutation must get a second write of its own — otherwise
    the newest state would never reach disk."""
    sctx = _sctx("running")
    entered = threading.Event()
    release = threading.Event()

    def _slow_write(path):
        entered.set()
        release.wait(5)
        open(path, "w").write("<helios/>")

    sctx.context.writeXML.side_effect = _slow_write

    persistence.queue_scenario_autosave(sctx)
    assert entered.wait(3), "the debounced save never started"

    # Arrives while the first write is mid-flight.
    persistence.queue_scenario_autosave(sctx)
    release.set()

    persistence.wait_for_scenario_saves(sctx)
    assert sctx.context.writeXML.call_count == 2


def test_scenarios_debounce_independently():
    """One scenario's burst must not coalesce away another's save."""
    a, b = _sctx("indep-a"), _sctx("indep-b")
    persistence.queue_scenario_autosave(a)
    persistence.queue_scenario_autosave(a)
    persistence.queue_scenario_autosave(b)

    persistence.wait_for_scenario_saves()
    assert a.context.writeXML.call_count == 1
    assert b.context.writeXML.call_count == 1
