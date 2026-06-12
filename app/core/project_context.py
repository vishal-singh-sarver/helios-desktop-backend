import time


class ProjectContext:
    """
    Per-project live state. One instance per open project.

    The `context` attribute holds the PyHelios C++ Context object
    which can be up to 10GB. This object is NEVER copied, serialized,
    or returned from a route. It lives here by reference and gets
    mutated in-place by service functions.

    Uses __slots__ for:
    - Fixed structure — typos raise AttributeError immediately
    - Faster attribute access — direct index lookup, no hash table
    - Lower memory overhead — no __dict__ per instance
    """

    __slots__ = (
        "project_id",
        "initialized",
        # PyHelios C++ objects — created lazily, destroyed on reset
        "context",
        "wpt",
        "plantarch",
        # Object registry — plain dict, mutated in-place
        "registry",
        "next_object_id",
        "default_material_label",
        # Caches — plain dicts, mutated in-place
        "geometry_cache",
        "gpu_geometry_cache",
        "gpu_children_cache",
        "script_object_counter",
        # Persisted scene objects (milestone 2) — DB row id → runtime
        # registry object_id, and which scenarios have been hydrated
        # from the DB into this context since startup/reset.
        "persisted_objects",
        "hydrated_scenarios",
    )

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.initialized = False
        self.context = None
        self.wpt = None
        self.plantarch = None
        self.registry = {}
        self.next_object_id = int(time.time() * 1000) % 1_000_000
        self.default_material_label = None
        self.geometry_cache = {}
        self.gpu_geometry_cache = {}
        self.gpu_children_cache = {}
        self.script_object_counter = 0
        self.persisted_objects = {}
        self.hydrated_scenarios = set()

    def reset(self):
        """Wipe all state for a fresh load. Old C++ context gets GC'd."""
        self.initialized = False
        self.context = None
        self.wpt = None
        self.plantarch = None
        self.registry = {}
        self.next_object_id = int(time.time() * 1000) % 1_000_000
        self.default_material_label = None
        self.geometry_cache = {}
        self.gpu_geometry_cache = {}
        self.gpu_children_cache = {}
        self.script_object_counter = 0
        self.persisted_objects = {}
        self.hydrated_scenarios = set()
