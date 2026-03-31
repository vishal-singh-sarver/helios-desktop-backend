import asyncio
from fastapi import APIRouter, HTTPException
from app.helios.context import get_context, PYHELIOS_AVAILABLE
from app.helios.context import Date, Time

router = APIRouter()


@router.post("/apply")
async def apply_timeseries(req: dict):
    def _do():
        from datetime import datetime
        ctx = get_context()
        variables = req.get("variables", [])
        rows = req.get("rows", [])
        warnings = []
        count = 0
        for var in variables:
            try:
                ctx.clearTimeseriesData(var["label"])
            except Exception:
                pass
        for row in rows:
            if not row.get("enabled", True):
                continue
            try:
                dt = datetime.fromisoformat(row["datetime"])
                date = Date(dt.year, dt.month, dt.day)
                time_obj = Time(dt.hour, dt.minute, dt.second)
                for var in variables:
                    label = var["label"]
                    val = row.get(label)
                    if val is not None:
                        ctx.addTimeseriesData(label, float(val), date, time_obj)
                count += 1
            except Exception as ex:
                warnings.append(str(ex))
        return {"success": True, "variables": len(variables), "datapoints": count, "warnings": warnings}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.get("")
async def get_timeseries():
    def _do():
        ctx = get_context()
        variables = ctx.listTimeseriesVariables()
        result = []
        for var in variables:
            datapoints = []
            try:
                for dp in ctx.getTimeseriesData(var):
                    datapoints.append({"value": dp.value, "date": str(dp.date), "time": str(dp.time)})
            except Exception:
                pass
            result.append({"label": var, "datapoints": datapoints})
        return {"variables": result}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))


@router.delete("")
async def delete_timeseries():
    def _do():
        ctx = get_context()
        for var in ctx.listTimeseriesVariables():
            try:
                ctx.clearTimeseriesData(var)
            except Exception:
                pass
        return {"success": True}
    try:
        return await asyncio.to_thread(_do)
    except Exception as e:
        raise HTTPException(500, str(e))
