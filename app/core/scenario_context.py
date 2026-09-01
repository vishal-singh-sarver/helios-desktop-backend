import time


class ScenarioContext:
    """
    Per-scenario live state. One instance per open scenario inside a project.

    Structurally mirrors ProjectContext, but scoped to a (project_id,
    scenario_id) pair. Each scenario owns its own PyHelios Context so that
    mutations in one scenario don't affect others under the same project.

    The `context` attribute holds the PyHelios C++ Context object. It is
    NEVER copied, serialized, or returned from a route. It lives here by
    reference and gets mutated in-place by service functions.

    Uses __slots__ for:
    - Fixed structure — typos raise AttributeError immediately
    - Faster attribute access — direct index lookup, no hash table
    - Lower memory overhead — no __dict__ per instance
    """

    __slots__ = (
        "project_id",
        "scenario_id",
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
        # Persisted scene objects (Phase 2) — DB scenario_object.id → runtime
        # registry object_id, and → live PyHelios compound-object id. `hydrated`
        # guards the one-time loadXML/build of this scenario's geometry.
        "persisted_objects",
        "ctx_objects",
        "hydrated",
        # Is context.xml on disk still a faithful copy of this context?
        #
        # A COUNTER PAIR, not a bool, and the difference matters. With a bool,
        # a mutation landing while a save is in flight would be cleared by that
        # save completing, and the change would never reach disk. `mutation_seq`
        # advances on every mutation; a save captures it BEFORE writing and
        # stores it in `saved_seq` only if the write succeeds. Anything that
        # mutates mid-write leaves the two unequal, so the scene stays dirty.
        #
        # Equal means the file matches — which lets /discard skip a full
        # writeXML it would otherwise repeat for no reason.
        "mutation_seq",
        "saved_seq",
        # THIS scenario's lock, guarding THIS scenario's PyHelios Context.
        #
        # It used to live on the SessionRegistry singleton, so every scenario in
        # every project in every session queued behind every other. Closing a
        # 700x700 project made opening a 4x4 one take 7.34s — none of it work,
        # all of it waiting on a save for a project the user had already closed.
        #
        # The engine never required that: two separate Contexts write XML
        # genuinely in parallel (7.6s of work in 4.0s wall) because PyHelios
        # releases the GIL and holds no global lock. The serialisation was ours,
        # and it belongs on the object it protects.
        "lock",
    )

    def __init__(self, project_id: str, scenario_id: str):
        self.project_id = project_id
        self.scenario_id = scenario_id
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
        self.ctx_objects = {}
        self.hydrated = False
        self.mutation_seq = 0
        self.saved_seq = 0
        # Imported here rather than at module scope: session_store imports this
        # module, so importing it back at the top would be a cycle.
        from app.core.session_store import ScenarioLock
        self.lock = ScenarioLock()

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
        self.ctx_objects = {}
        self.hydrated = False
        self.mutation_seq = 0
        self.saved_seq = 0
