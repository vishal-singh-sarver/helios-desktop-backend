import functools
import threading
from contextlib import contextmanager

from app.core.project_context import ProjectContext
from app.core.scenario_context import ScenarioContext


class ScenarioLock:
    """Many concurrent readers of a PyHelios context, or one mutator.

    An exclusive lock here made the autosave and the geometry read block each
    other, though NEITHER mutates the context: writeXML serialises it to a file,
    pack_primitives reads primitive data. A save therefore held off the very
    fetch that draws the scene, so a new ground did not appear until its write
    had finished — the wait the deferred save was meant to remove, moved from
    the response to the render. Mutations still exclude everything.

    write() is re-entrant for its holder — the mutation helpers nest
    (_apply_assignment_change → _rebuild → _teardown + _build) — and that holder
    may take read() too, so a mutator can serialise without deadlocking itself.

    A waiting mutation blocks NEW readers: viewport polling is continuous, and
    without that an edit could wait behind an unbroken run of reads forever.
    """

    def __init__(self):
        self._cond = threading.Condition(threading.Lock())
        self._readers = 0
        self._owner: int | None = None      # thread holding write()
        self._depth = 0                     # its re-entrancy depth
        self._waiting_writers = 0

    @contextmanager
    def read(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner != me:           # its own holder never waits
                while self._owner is not None or self._waiting_writers:
                    self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextmanager
    def write(self):
        me = threading.get_ident()
        with self._cond:
            if self._owner == me:
                self._depth += 1
            else:
                self._waiting_writers += 1
                try:
                    while self._owner is not None or self._readers:
                        self._cond.wait()
                finally:
                    self._waiting_writers -= 1
                self._owner = me
                self._depth = 1
        try:
            yield
        finally:
            with self._cond:
                self._depth -= 1
                if self._depth == 0:
                    self._owner = None
                    self._cond.notify_all()


class SessionRegistry:
    """
    Singleton registry: session → project → [ScenarioContext] / ProjectContext.

    This is the ONLY place that holds references to live context objects.
    All access goes through this class.

    Two parallel layers coexist:
      - _store:     session_id → project_id → ProjectContext    (legacy per-project context; used by all non-weather routers)
      - _scenarios: session_id → project_id → scenario_id → ScenarioContext
                                                              (per-scenario context; used by weather routes)

    The two layers are independent. Removing a project wipes both.
    """

    def __init__(self):
        # {session_id: {project_id: ProjectContext}}
        self._store: dict[str, dict[str, ProjectContext]] = {}
        # {session_id: {project_id: {scenario_id: ScenarioContext}}}
        self._scenarios: dict[str, dict[str, dict[str, ScenarioContext]]] = {}
        # Guards a scenario's shared PyHelios context (geometry + weather live
        # in the same sctx.context). Mutations take .write(); the autosave and
        # the geometry reads take .read() and so no longer block each other.
        self._scenario_lock = ScenarioLock()

    # ── Session level ────────────────────────────────────────────────

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._store

    def get_or_create_session(self, session_id: str) -> dict[str, ProjectContext]:
        """Return the project dict for this session. Creates if new."""
        if session_id not in self._store:
            self._store[session_id] = {}
        return self._store[session_id]

    def remove_session(self, session_id: str) -> None:
        """Remove an entire session and all its projects + scenarios from memory."""
        self._store.pop(session_id, None)
        self._scenarios.pop(session_id, None)

    # ── Project level (legacy, still used by non-weather routers) ────

    def get_or_create_context(
        self, session_id: str, project_id: str
    ) -> ProjectContext:
        """
        Look up a live project. If it doesn't exist, create a fresh one.
        The caller always gets a ProjectContext back.
        """
        session = self.get_or_create_session(session_id)
        if project_id not in session:
            session[project_id] = ProjectContext(project_id)
        return session[project_id]

    def get_context(
        self, session_id: str, project_id: str
    ) -> ProjectContext | None:
        """Look up a live project. Returns None if not in memory."""
        return self._store.get(session_id, {}).get(project_id)

    def remove_project(self, session_id: str, project_id: str) -> None:
        """Remove a project from memory. Also wipes its scenarios."""
        self._store.get(session_id, {}).pop(project_id, None)
        self.remove_all_scenarios_for_project(session_id, project_id)

    def list_projects(self, session_id: str) -> list[str]:
        """Return all project_ids currently in memory for this session."""
        return list(self._store.get(session_id, {}).keys())

    # ── Scenario level ───────────────────────────────────────────────

    def get_or_create_scenario_context(
        self, session_id: str, project_id: str, scenario_id: str
    ) -> ScenarioContext:
        """
        Look up a live scenario. If it doesn't exist, create a fresh one.
        The caller always gets a ScenarioContext back.
        """
        if session_id not in self._scenarios:
            self._scenarios[session_id] = {}
        if project_id not in self._scenarios[session_id]:
            self._scenarios[session_id][project_id] = {}
        proj_scenarios = self._scenarios[session_id][project_id]
        if scenario_id not in proj_scenarios:
            proj_scenarios[scenario_id] = ScenarioContext(project_id, scenario_id)
        return proj_scenarios[scenario_id]

    def get_scenario_context(
        self, session_id: str, project_id: str, scenario_id: str
    ) -> ScenarioContext | None:
        """Look up a live scenario. Returns None if not in memory."""
        return (
            self._scenarios.get(session_id, {})
            .get(project_id, {})
            .get(scenario_id)
        )

    def remove_scenario(
        self, session_id: str, project_id: str, scenario_id: str
    ) -> None:
        """Remove a single scenario from memory."""
        self._scenarios.get(session_id, {}).get(project_id, {}).pop(scenario_id, None)

    def remove_all_scenarios_for_project(
        self, session_id: str, project_id: str
    ) -> None:
        """Wipe every scenario for this project. Called on project delete."""
        self._scenarios.get(session_id, {}).pop(project_id, None)


def with_context_write_lock(fn):
    """Serialise a function that MUTATES a scenario's PyHelios context.

    Weather mutates sctx.context directly (loadTabularTimeseriesData,
    addTimeseriesData, clearTimeseriesData, deleteTimeseriesVariable) and took
    no lock at all. That was survivable while its save ran inline on the same
    thread — mutation and serialisation could not overlap. Once the save moved
    to the queue, they could: the queued writeXML holds .read() while it walks
    the context, and an unlocked weather mutation would rewrite it underneath.
    Measured with .read() held for 1.5s: a geometry PATCH correctly waited
    1.51s, a weather clear_data went through in 0.01s.

    Re-entrant for its holder, so a mutator calling another is safe.
    """
    @functools.wraps(fn)
    def _wrapper(*args, **kwargs):
        with registry._scenario_lock.write():
            return fn(*args, **kwargs)
    return _wrapper


# Module-level singleton
registry = SessionRegistry()
