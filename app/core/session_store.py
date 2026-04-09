from app.core.project_context import ProjectContext


class SessionRegistry:
    """
    Singleton registry: session_id -> project_id -> ProjectContext.

    This is the ONLY place that holds references to live ProjectContext objects.
    All access goes through this class.
    """

    def __init__(self):
        # {session_id: {project_id: ProjectContext}}
        self._store: dict[str, dict[str, ProjectContext]] = {}

    # ── Session level ────────────────────────────────────────────────

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._store

    def get_or_create_session(self, session_id: str) -> dict[str, ProjectContext]:
        """Return the project dict for this session. Creates if new."""
        if session_id not in self._store:
            self._store[session_id] = {}
        return self._store[session_id]

    def remove_session(self, session_id: str) -> None:
        """Remove an entire session and all its projects from memory."""
        self._store.pop(session_id, None)

    # ── Project level ────────────────────────────────────────────────

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
        """Remove a project from memory. The ProjectContext gets GC'd."""
        self._store.get(session_id, {}).pop(project_id, None)

    def list_projects(self, session_id: str) -> list[str]:
        """Return all project_ids currently in memory for this session."""
        return list(self._store.get(session_id, {}).keys())


# Module-level singleton
registry = SessionRegistry()
