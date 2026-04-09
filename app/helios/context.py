"""
PyHelios singleton management — path setup, imports, and accessors.

Module-level code runs once on first import (at startup via lifespan).
All routers import get_context() / get_wpt() / get_plantarch() from here.
"""
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
            _build_script = Path(__file__).resolve().parent.parent.parent / "scripts" / "build_pyhelios.sh"
            if _build_script.exists():
                print("[pyhelios] Building from source (this may take a few minutes)...")
                _result = subprocess.run([str(_build_script)], cwd=str(_pyhelios_src.parent), timeout=600)
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
    from pyhelios import Context, WeberPennTree, WPTType
    from pyhelios.types import vec2, vec3, int2, RGBcolor, RGBAcolor, SphericalCoord, Date, Time
    PYHELIOS_AVAILABLE: bool = True
except Exception as e:
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
