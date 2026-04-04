from datetime import datetime
from app.helios.context import get_context, Date, Time


def apply_timeseries(variables: list, rows: list) -> dict:
    ctx = get_context()
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


def get_timeseries() -> dict:
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


def delete_timeseries() -> dict:
    ctx = get_context()
    for var in ctx.listTimeseriesVariables():
        try:
            ctx.clearTimeseriesData(var)
        except Exception:
            pass
    return {"success": True}
