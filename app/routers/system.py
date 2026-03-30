from fastapi import APIRouter
from app.core.config import settings

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
    }


@router.get("/version")
async def version():
    return {"version": settings.backend_version}


# TODO: /api/pyhelios-info — implement in Step 3
