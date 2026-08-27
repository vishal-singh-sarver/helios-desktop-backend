"""
PyHelios singleton management — path setup, imports, and accessors.

Module-level code runs once on first import (at startup via lifespan).
All routers import get_context() / get_wpt() / get_plantarch() from here.
"""
import ctypes
import logging
import sys
import uuid as _uuid_mod
from pathlib import Path
from fastapi import HTTPException

from app.core.config import settings

# ── Session ID ────────────────────────────────────────────────────────────────
# Changes on every backend restart. Frontend uses this to detect stale state.
session_id: str = _uuid_mod.uuid4().hex

# ── PyHelios path setup ───────────────────────────────────────────────────────
_PYHELIOS_USE_SOURCE = not settings.pyhelios_use_pip
_PYHELIOS_SOURCE_PATH: str | None = None
_PYHELIOS_STALE: bool = False

if _PYHELIOS_USE_SOURCE:
    _pyhelios_src = settings.pyhelios_auto_source_path
    if (_pyhelios_src / "pyhelios" / "__init__.py").exists():
        _PYHELIOS_SOURCE_PATH = str(_pyhelios_src)
        if _PYHELIOS_SOURCE_PATH not in sys.path:
            sys.path.insert(0, _PYHELIOS_SOURCE_PATH)
        print(f"[pyhelios] Using source: {_PYHELIOS_SOURCE_PATH}")

        _platform = sys.platform
        _lib_name = (
            "libhelios.dylib" if _platform == "darwin"
            else "libhelios.dll" if _platform == "win32"
            else "libhelios.so"
        )
        _lib_path = _pyhelios_src / "pyhelios_build" / "build" / "lib" / _lib_name

        _needs_build = False
        if not _lib_path.exists():
            print(f"[pyhelios] Native library not found at {_lib_path}")
            _needs_build = True
        else:
            _lib_mtime = _lib_path.stat().st_mtime
            _newest_source = 0.0
            for _src_dir in [_pyhelios_src / "helios-core", _pyhelios_src / "native"]:
                if _src_dir.exists():
                    for _f in _src_dir.rglob("*.[ch]pp"):
                        _newest_source = max(_newest_source, _f.stat().st_mtime)
                    for _f in _src_dir.rglob("*.h"):
                        _newest_source = max(_newest_source, _f.stat().st_mtime)
            if _newest_source > _lib_mtime:
                _needs_build = True
                print("[pyhelios] Native library is stale (source files are newer).")

        if _needs_build:
            import subprocess
            _scripts_dir = Path(__file__).resolve().parent.parent.parent / "scripts"
            # Pick the platform-native build script: PowerShell on Windows,
            # bash elsewhere. (A .sh can't be executed directly on Windows.)
            if sys.platform == "win32":
                _build_script = _scripts_dir / "build_pyhelios.ps1"
                _build_cmd = [
                    "powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                    "-File", str(_build_script),
                ]
            else:
                _build_script = _scripts_dir / "build_pyhelios.sh"
                _build_cmd = [str(_build_script)]
            if _build_script.exists():
                print("[pyhelios] Building from source (this may take a few minutes)...")
                # stdin=DEVNULL, never inherited. The Electron app spawns the
                # backend with a PIPE on stdin as a liveness signal and never
                # writes to it (main/backend-manager.ts); a child that inherits
                # that pipe and reads it blocks forever, and this one runs
                # during module import, so the whole backend hangs before it
                # can serve. DEVNULL gives the build script an immediate EOF.
                _result = subprocess.run(
                    _build_cmd, cwd=str(_pyhelios_src.parent), timeout=600,
                    stdin=subprocess.DEVNULL,
                )
                if _result.returncode == 0 and _lib_path.exists():
                    print("[pyhelios] Build complete.")
                else:
                    print("[pyhelios] WARNING: Build failed. Native features unavailable.")
                    _PYHELIOS_STALE = True
            else:
                print(f"[pyhelios] WARNING: Build script not found at {_build_script}")
                _PYHELIOS_STALE = True
    else:
        print(f"[pyhelios] WARNING: Submodule not found at {_pyhelios_src}")
        print("  Run: git submodule update --init --recursive")
        _PYHELIOS_USE_SOURCE = False
else:
    print("[pyhelios] Using pip wheel (pyhelios3d)")

# ── PyHelios imports ──────────────────────────────────────────────────────────
try:
    import pyhelios
    # If it's a namespace package (common on Windows editable installs), 
    # we need to force import from the submodules
    if getattr(pyhelios, "__file__", None) is None:
        from pyhelios import Context, WeberPennTree, WPTType
    else:
        from pyhelios import Context, WeberPennTree, WPTType
    
    from pyhelios.types import vec2, vec3, int2, RGBcolor, RGBAcolor, SphericalCoord, Date, Time
    PYHELIOS_AVAILABLE: bool = True
except Exception as e:
    # Final fallback attempt: try direct submodule imports
    try:
        from pyhelios.Context import Context
        from pyhelios.WeberPennTree import WeberPennTree, WPTType
        from pyhelios.types import vec2, vec3, int2, RGBcolor, RGBAcolor, SphericalCoord, Date, Time
        PYHELIOS_AVAILABLE = True
    except Exception as e2:
        PYHELIOS_AVAILABLE = False
        Context = WeberPennTree = WPTType = None
        vec2 = vec3 = int2 = RGBcolor = RGBAcolor = SphericalCoord = Date = Time = None
        print(f"[pyhelios] WARNING: Not available — {e}")

try:
    from pyhelios import PlantArchitecture
    PLANTARCH_AVAILABLE: bool = True
except Exception:
    PLANTARCH_AVAILABLE = False
    PlantArchitecture = None


def get_context(pctx):
    """Return the project's Context singleton, creating it on first call."""
    if pctx.context is None:
        if not PYHELIOS_AVAILABLE:
            raise HTTPException(503, "PyHelios not available")
        pctx.context = Context()
    return pctx.context


def get_wpt(pctx):
    """Return the project's WeberPennTree singleton."""
    if pctx.wpt is None:
        pctx.wpt = WeberPennTree(get_context(pctx))
    return pctx.wpt


def get_plantarch(pctx):
    """Return the project's PlantArchitecture singleton."""
    if pctx.plantarch is None:
        if not PLANTARCH_AVAILABLE:
            raise HTTPException(503, "PlantArchitecture plugin not available")
        pctx.plantarch = PlantArchitecture(get_context(pctx))
    return pctx.plantarch


# Probed ONCE, not per call. glibc only: absent on macOS and musl, and there is
# no Windows equivalent, so this is None on those and release_memory is a no-op.
# CDLL(None) opens the current process rather than guessing a soname —
# "libc.so.6" is wrong on musl and does not exist on macOS at all.
try:
    _malloc_trim = ctypes.CDLL(None).malloc_trim
    # int malloc_trim(size_t pad). Declared rather than left to ctypes'
    # default int-sized argument, which is the wrong width for size_t on 64-bit.
    _malloc_trim.argtypes = [ctypes.c_size_t]
    _malloc_trim.restype = ctypes.c_int
except Exception:       # noqa: BLE001 — see below; MUST NOT be narrowed
    # DELIBERATELY BROAD, and this module runs it at import time.
    #
    # A narrow (OSError, AttributeError) looked right and was a Windows
    # ship-blocker: CPython's CDLL.__init__ takes an `nt` branch that runs
    # `if '/' in name or '\\' in name` BEFORE dlopen, and with name=None that
    # raises TypeError — which would escape, abort the import of this module,
    # and stop the backend booting at all. On the one platform none of us can
    # test locally.
    #
    # Reclaiming memory is an optimisation. Nothing it does may ever be able to
    # prevent the process from starting, so every failure mode ends here.
    _malloc_trim = None

_release_log = logging.getLogger("helios.memory")


def release_memory() -> None:
    """Return a dropped context's memory to the OS. Call AFTER the last ref dies.

    Dropping a ScenarioContext frees the C++ context, but freeing is not
    returning: glibc keeps the pages in its arena and RSS does not move. On a
    1000x1000 ground, measured:

        context built                      1742.7 MB
        after del ctx + gc.collect()       1804.6 MB   <- nothing given back
        after malloc_trim(0)                 65.9 MB

    So opening project A, discarding it, then opening B costs A+B resident
    rather than max(A,B) — which is why the kernel SIGKILLed the server on the
    second project. glibc CAN shrink via sbrk when the freed block sits at the
    top of the heap, which is why a single open/close loop looks fine on a
    bench and only the discard-then-open case fails.

    Safe with other scenarios still open: it releases FREE pages and cannot
    touch a live allocation. Verified on a still-open 700x700 scenario after a
    trim — 490,000 UUIDs read and a full writeXML, both fine.

    Cost is 62 ms on a 1.7 GB heap, 5 ms on a small one. Only ever called on a
    release path, never on a request that is doing work.

    MUST come after the last reference is gone. A local still holding the sctx
    keeps the memory live and the trim reclaims nothing.
    """
    if _malloc_trim is None:
        return
    try:
        _malloc_trim(0)
    except Exception:                   # noqa: BLE001 — reclaiming is best-effort
        _release_log.debug("malloc_trim failed", exc_info=True)


def reset_context(pctx) -> None:
    """Destroy the project's context singletons."""
    pctx.context = None
    pctx.wpt = None
    pctx.plantarch = None


def init_pyhelios() -> None:
    """Called at startup to log PyHelios availability."""
    if PYHELIOS_AVAILABLE:
        try:
            import pyhelios
            ver = getattr(pyhelios, "__version__", "unknown")
            print(f"[pyhelios] Available — version {ver}")
        except Exception:
            print("[pyhelios] Available")
    else:
        print("[pyhelios] Not available — geometry endpoints will return 503")
