"""A weather upload must not parse its CSV on the event loop.

`upload_file` is the only `async def` in the weather router — it awaits the
upload — so it is the only one that can block the loop. The rest are plain
`def`, which FastAPI runs in a threadpool. Parsing a real weather file inline
froze every other request for the duration.
"""
import threading
import time
from uuid import uuid4

from app.services import weather_header_service

CSV = (b"date,time,temperature,humidity\n"
       b"2023-07-13,10:00:00,22.5,65\n"
       b"2023-07-13,11:00:00,23.8,62\n")

PARSE_SECONDS = 1.5


def test_a_slow_upload_does_not_stall_other_requests(client, monkeypatch):
    sh = f"session_{uuid4().hex[:8]}"
    d = client.post("/api/project/create", json={
        "name": f"WL_{uuid4().hex[:6]}", "latitude": 28.6, "longitude": 77.2,
    }, headers={"session-id": sh}).json()
    pid, sid = d["project_id"], d["main_scenario_id"]
    h = {"session-id": sh}

    from app.routers import weather as weather_router
    real = weather_router.weather_service.upload_file

    def _slow(sctx, content):
        time.sleep(PARSE_SECONDS)      # stand in for a real weather file
        return real(sctx, content)

    monkeypatch.setattr(weather_router.weather_service, "upload_file", _slow)

    timings = {}

    def _upload():
        t = time.monotonic()
        r = client.post(f"/api/weather/project/{pid}/scenario/{sid}/uploadfile",
                        headers=h, files={"file": ("t.csv", CSV, "text/csv")})
        timings["upload"] = time.monotonic() - t
        timings["status"] = r.status_code

    u = threading.Thread(target=_upload)
    u.start()
    time.sleep(0.3)                    # let the parse get under way

    t = time.monotonic()
    c = client.get("/api/catalog/object-types")
    timings["catalog"] = time.monotonic() - t

    u.join(timeout=30)

    assert c.status_code == 200
    assert timings["upload"] >= PARSE_SECONDS, f"the slow parse never ran: {timings}"
    assert timings["catalog"] < PARSE_SECONDS * 0.5, (
        f"the event loop was frozen by the upload — an unrelated request waited "
        f"{timings['catalog']:.2f}s behind a {PARSE_SECONDS}s parse: {timings}")
