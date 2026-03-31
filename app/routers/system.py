from fastapi import APIRouter, Request
from app.core.config import settings
from app.helios import context as helios_ctx

router = APIRouter(tags=["system"])


@router.get("/")
async def root():
    return {"message": "HeliosGUI API", "version": settings.backend_version}


@router.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.backend_version,
        "env": settings.app_env,
        "pyhelios_available": helios_ctx.PYHELIOS_AVAILABLE,
        "session_id": helios_ctx.session_id,
    }


@router.get("/version")
async def version():
    return {"version": settings.backend_version, "session_id": helios_ctx.session_id}


@router.get("/api/pyhelios-info")
async def pyhelios_info():
    info = {
        "source": helios_ctx._PYHELIOS_USE_SOURCE,
        "source_path": helios_ctx._PYHELIOS_SOURCE_PATH,
        "stale": helios_ctx._PYHELIOS_STALE,
        "available": helios_ctx.PYHELIOS_AVAILABLE,
        "plantarch_available": helios_ctx.PLANTARCH_AVAILABLE,
    }
    if helios_ctx.PYHELIOS_AVAILABLE:
        try:
            import pyhelios
            from pathlib import Path
            info["version"] = getattr(pyhelios, "__version__", "unknown")
            info["location"] = str(Path(pyhelios.__file__).parent)
        except Exception:
            pass
    return info
