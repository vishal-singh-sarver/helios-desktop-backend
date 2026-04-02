import asyncio
import json
import logging
import queue
import traceback
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from app.helios.context import PLANTARCH_AVAILABLE
from app.schemas.tree import CanopyBuildRequest
from app.services import canopy_service

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/species")
async def get_plant_species():
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")
    try:
        return await asyncio.to_thread(canopy_service.get_plant_species)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.post("/canopy")
async def build_canopy(req: CanopyBuildRequest):
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")
    try:
        return await asyncio.to_thread(canopy_service.build_canopy, req)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))


@router.post("/canopy/stream")
async def build_canopy_stream(req: CanopyBuildRequest):
    if not PLANTARCH_AVAILABLE:
        raise HTTPException(503, "PlantArchitecture plugin not available")

    progress_queue: queue.Queue = queue.Queue()

    async def _stream():
        import asyncio as _asyncio
        loop = _asyncio.get_event_loop()
        loop.run_in_executor(None, canopy_service.build_canopy_bg, req, progress_queue)
        while True:
            await _asyncio.sleep(0.1)
            while not progress_queue.empty():
                event = progress_queue.get_nowait()
                try:
                    payload = json.dumps(event)
                except Exception as je:
                    logger.error("json.dumps failed: %r for event keys=%s", je, list(event.keys()))
                    traceback.print_exc()
                    yield f"data: {json.dumps({'error': f'Serialization error: {je}'})}\n\n"
                    return
                yield f"data: {payload}\n\n"
                if event.get("progress", 0) >= 1.0 or "error" in event:
                    return

    return StreamingResponse(_stream(), media_type="text/event-stream")


@router.get("/canopy")
async def build_canopy_get(req: CanopyBuildRequest):
    """Alias kept for frontend compatibility."""
    return await build_canopy(req)
