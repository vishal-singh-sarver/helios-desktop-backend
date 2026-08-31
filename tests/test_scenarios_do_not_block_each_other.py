"""One scenario's work must not stall another's.

Closing a 1 GB project starts a writeXML that can run for ~16s. Today it holds
the process's ONLY lock and its ONLY save worker, so the next project the user
opens waits for a project they already closed. Measured on a 700x700 ground:

    open a 4x4 ground, system idle              0.00s
    open a 4x4 ground, while the big one closes 7.34s
    the small project's own save, alone        0.00s
    the small project's own save, behind it    7.58s

Neither number is work. The engine has no such limitation — two separate
Contexts write XML genuinely in parallel (7.6s of work in 4.0s wall) because
PyHelios releases the GIL and holds no global lock of its own. The serialisation
is entirely ours, so these tests pin the two resources to the scenario they
belong to rather than to the process.

Coordinated with Events rather than sleeps: a timing-based version of this
passes or fails on machine speed, which is worse than no test.
"""
import threading
import time

import pytest

from app.core.scenario_context import ScenarioContext
from app.core.session_store import registry
from app.helios import persistence

TIMEOUT = 10.0          # generous: we are asserting "did not block", not speed


def _scenario(session: str, project: str, scenario: str) -> ScenarioContext:
    sctx = ScenarioContext(project, scenario)
    sctx.initialized = sctx.hydrated = True
    registry._scenarios.setdefault(session, {}).setdefault(project, {})[scenario] = sctx
    return sctx


@pytest.fixture(autouse=True)
def _clean():
    yield
    for s in ("user-A", "user-B"):
        registry._scenarios.pop(s, None)


def test_a_long_save_does_not_block_another_scenario():
    """Problem 1: the lock.

    A is mid-writeXML. B — a different scenario, different project, different
    user — must be able to take its own lock immediately.
    """
    a = _scenario("user-A", "proj-A", "scen-A")
    b = _scenario("user-B", "proj-B", "scen-B")

    a_holding = threading.Event()
    release_a = threading.Event()
    b_got_lock = threading.Event()

    def hold_a():
        with a.lock.read():           # what a queued save holds
            a_holding.set()
            release_a.wait(TIMEOUT)

    t = threading.Thread(target=hold_a, daemon=True)
    t.start()
    assert a_holding.wait(TIMEOUT), "A never started"

    def open_b():
        with b.lock.write():          # what opening a scenario needs
            b_got_lock.set()

    tb = threading.Thread(target=open_b, daemon=True)
    tb.start()

    got = b_got_lock.wait(3.0)
    release_a.set()
    t.join(TIMEOUT)
    tb.join(TIMEOUT)

    assert got, (
        "scenario B could not start while an unrelated scenario A was saving — "
        "the lock is still shared across scenarios")


def test_a_long_save_does_not_delay_another_scenarios_save():
    """Problem 2: the save queue.

    A's save is slow. B's save must not sit behind it in the same worker.
    """
    a = _scenario("user-A", "proj-A", "scen-A")
    b = _scenario("user-B", "proj-B", "scen-B")

    a_started = threading.Event()
    release_a = threading.Event()
    b_done = threading.Event()

    real = persistence.trigger_scenario_autosave

    def fake(sctx):
        if sctx is a:
            a_started.set()
            release_a.wait(TIMEOUT)   # a long writeXML
        else:
            b_done.set()

    persistence.trigger_scenario_autosave = fake
    try:
        persistence.queue_scenario_autosave(a)
        assert a_started.wait(TIMEOUT), "A's save never started"
        persistence.queue_scenario_autosave(b)

        finished = b_done.wait(3.0)
        release_a.set()
        time.sleep(0.2)
    finally:
        persistence.trigger_scenario_autosave = real
        persistence.wait_for_scenario_saves()

    assert finished, (
        "scenario B's save waited for scenario A's — the save pool is still "
        "one worker shared by every scenario")


def test_the_same_scenario_is_still_serialised():
    """The control, and the reason the lock exists.

    Splitting per scenario must NOT let two threads mutate ONE context at once.
    """
    a = _scenario("user-A", "proj-A", "scen-A")

    holding = threading.Event()
    release = threading.Event()
    second_got_in = threading.Event()

    def first():
        with a.lock.write():
            holding.set()
            release.wait(TIMEOUT)

    t = threading.Thread(target=first, daemon=True)
    t.start()
    assert holding.wait(TIMEOUT)

    def second():
        with a.lock.write():          # SAME scenario — must wait
            second_got_in.set()

    t2 = threading.Thread(target=second, daemon=True)
    t2.start()

    leaked = second_got_in.wait(1.0)
    release.set()
    t.join(TIMEOUT)
    t2.join(TIMEOUT)

    assert not leaked, (
        "two threads mutated the same scenario's context at once — the split "
        "went too far")


def test_write_is_still_reentrant_for_its_holder():
    """Nested mutation helpers depend on this — _apply_assignment_change ->
    _rebuild -> _teardown + _build all nest, and a mutator may also read."""
    a = _scenario("user-A", "proj-A", "scen-A")

    with a.lock.write():
        with a.lock.write():          # re-entrant
            with a.lock.read():       # holder may read
                pass


def test_init_does_not_wait_for_another_scenarios_save():
    """The "Saving scenario" stage of /init must be scoped to one scenario.

    init drains the save queue before reporting ready, so the client's next
    call cannot land mid-write. Draining EVERY scenario's queue made opening a
    1.3 KB project sit on "Saving scenario" until a 299 MB project's save had
    finished — observed in the app, and not caught by the lock tests because
    it is the save pool, not the lock.
    """
    from app.services import scene_object_service as sos

    a = _scenario("user-A", "proj-A", "scen-A")
    b = _scenario("user-B", "proj-B", "scen-B")

    a_started = threading.Event()
    release_a = threading.Event()
    real = persistence.trigger_scenario_autosave

    def fake(sctx):
        if sctx is a:
            a_started.set()
            release_a.wait(TIMEOUT)

    persistence.trigger_scenario_autosave = fake
    try:
        persistence.queue_scenario_autosave(a)
        assert a_started.wait(TIMEOUT), "A's save never started"

        done = threading.Event()
        threading.Thread(
            target=lambda: (sos.wait_for_saves(b), done.set()), daemon=True).start()
        finished = done.wait(3.0)
        release_a.set()
    finally:
        persistence.trigger_scenario_autosave = real
        persistence.wait_for_scenario_saves()

    assert finished, (
        "init's save-drain waited for an unrelated scenario — this is the "
        "'Saving scenario' stage hanging on another project's write")
