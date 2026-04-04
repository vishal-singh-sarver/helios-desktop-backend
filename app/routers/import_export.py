import asyncio
from fastapi import APIRouter, HTTPException

from app.schemas.geometry import ImportRequest
from app.services import import_service

router = APIRouter()


@router.post("/import/obj")
async def import_obj(req: ImportRequest):
    try:
        return await asyncio.to_thread(import_service.import_obj, req)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.post("/import/ply")
async def import_ply(req: ImportRequest):
    try:
        return await asyncio.to_thread(import_service.import_ply, req)
    except Exception as e:
        raise HTTPException(500, str(e))
