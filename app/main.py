import logging

import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.lifespan import lifespan
from app.routers import (
    system,
    project,
    scenario,
    geometry,
    objects,
    tree,
    plantarch,
    materials,
    transforms,
    timeseries,
    weather,
    import_export,
    scripting,
    helios_data_type,
    data_unit,
    catalog,
    scene_objects,
    material_library,
    textures,
)

app = FastAPI(
    title="HeliosGUI API",
    version=settings.backend_version,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Stale build warning header (added after middleware setup to avoid import-time issues)
@app.middleware("http")
async def stale_pyhelios_header(request: Request, call_next):
    response = await call_next(request)
    from app.helios.context import _PYHELIOS_STALE
    if _PYHELIOS_STALE:
        response.headers["X-PyHelios-Stale"] = "true"
    return response


# Anything slower than this gets a line of its own. The access log records every
# request; this makes the slow ones findable without reading all of them, and
# turns "the app feels slow" into a path and a number.
SLOW_REQUEST_SECONDS = 2.0


@app.middleware("http")
async def log_slow_requests(request: Request, call_next):
    started = time.monotonic()
    response = await call_next(request)
    elapsed = time.monotonic() - started
    if elapsed >= SLOW_REQUEST_SECONDS:
        logging.getLogger("app.slow").warning(
            "[slow]    %.1fs  %s %s -> %s",
            elapsed, request.method, request.url.path, response.status_code)
    return response

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(system.router)
app.include_router(project.router,       prefix="/api/project",    tags=["project"])
app.include_router(scenario.router,      prefix="/api/project",    tags=["scenario"])
app.include_router(geometry.router,      prefix="/api/geometry",   tags=["geometry"])
app.include_router(objects.router,       prefix="/api/objects",    tags=["objects"])
app.include_router(tree.router,          prefix="/api/tree",       tags=["tree"])
app.include_router(plantarch.router,     prefix="/api/plantarch",  tags=["plantarch"])
app.include_router(materials.router,     prefix="/api/materials",  tags=["materials"])
app.include_router(transforms.router,    prefix="/api/geometry",   tags=["transforms"])
app.include_router(timeseries.router,    prefix="/api/timeseries", tags=["timeseries"])
app.include_router(weather.router,       prefix="/api/weather",    tags=["weather"])
app.include_router(import_export.router, prefix="/api",            tags=["import"])
app.include_router(scripting.router,     prefix="/api/script",     tags=["scripting"])
app.include_router(helios_data_type.router, prefix="/api/data-types", tags=["catalog"])
app.include_router(data_unit.router,        prefix="/api/data-units", tags=["catalog"])
# Milestone 2 — persisted geometry, material-group library, group assignment
# and scenario material-sync (migration 022).
app.include_router(catalog.router,          prefix="/api/catalog",    tags=["m2-catalog"])
app.include_router(scene_objects.router,    prefix="/api/geometry",   tags=["m2-geometry"])
app.include_router(material_library.router, prefix="/api/materials",  tags=["m2-materials"])
# Texture image serving for the 3D viewport (GET /api/textures/serve?path=…).
app.include_router(textures.router,         prefix="/api/textures",   tags=["textures"])
