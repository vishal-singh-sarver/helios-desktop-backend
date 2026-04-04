from fastapi import APIRouter, HTTPException

from app.helios.context import PYHELIOS_AVAILABLE
from app.schemas.scripting import ScriptExecuteRequest
from app.services import scripting_service

router = APIRouter()


@router.post("/execute")
async def execute_script(req: ScriptExecuteRequest):
    if not PYHELIOS_AVAILABLE:
        raise HTTPException(503, "PyHelios not available")
    try:
        return await scripting_service.execute_script(req.code, req.timeout)
    except Exception as e:
        raise HTTPException(500, str(e))
