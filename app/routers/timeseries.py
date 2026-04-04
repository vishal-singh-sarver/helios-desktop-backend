import asyncio
from fastapi import APIRouter, HTTPException

from app.services import timeseries_service

router = APIRouter()


@router.post("/apply")
async def apply_timeseries(req: dict):
    try:
        return await asyncio.to_thread(
            timeseries_service.apply_timeseries,
            req.get("variables", []),
            req.get("rows", []),
        )
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("")
async def get_timeseries():
    try:
        return await asyncio.to_thread(timeseries_service.get_timeseries)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("")
async def delete_timeseries():
    try:
        return await asyncio.to_thread(timeseries_service.delete_timeseries)
    except Exception as e:
        raise HTTPException(500, str(e))
