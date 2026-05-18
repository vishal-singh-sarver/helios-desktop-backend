import threading
from app.core.project_context import ProjectContext
from app.core.scenario_context import ScenarioContext


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
        # Lock to prevent race conditions during scenario initialization/loading
        self._scenario_lock = threading.Lock()

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


# Module-level singleton
registry = SessionRegistry()
