from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import settings
from app.core.lifespan import lifespan
from app.routers import (
    system,
    project,
    geometry,
    objects,
    tree,
    canopy,
    plantarch,
    materials,
    transforms,
    timeseries,
    import_export,
    scripting,
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

# ── Routers ────────────────────────────────────────────────────────────────
app.include_router(system.router)
app.include_router(project.router,      prefix="/api/project",      tags=["project"])
app.include_router(geometry.router,     prefix="/api/geometry",     tags=["geometry"])
app.include_router(objects.router,      prefix="/api/objects",      tags=["objects"])
app.include_router(tree.router,         prefix="/api/tree",         tags=["tree"])
app.include_router(canopy.router,       prefix="/api/canopy",       tags=["canopy"])
app.include_router(plantarch.router,    prefix="/api/plantarch",    tags=["plantarch"])
app.include_router(materials.router,    prefix="/api/materials",    tags=["materials"])
app.include_router(transforms.router,   prefix="/api/geometry",     tags=["transforms"])
app.include_router(timeseries.router,   prefix="/api/timeseries",   tags=["timeseries"])
app.include_router(import_export.router,prefix="/api",              tags=["import_export"])
app.include_router(scripting.router,    prefix="/api/script",       tags=["scripting"])
