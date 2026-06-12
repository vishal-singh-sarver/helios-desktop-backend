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
# Milestone 2 — persisted geometry, material library, assignment (spec in
# helios_gui repo: docs/api/milestone-2-materials-geometry.md)
app.include_router(catalog.router,          prefix="/api/catalog",    tags=["m2-catalog"])
app.include_router(scene_objects.router,    prefix="/api/geometry",   tags=["m2-geometry"])
app.include_router(material_library.router, prefix="/api/materials",  tags=["m2-materials"])
