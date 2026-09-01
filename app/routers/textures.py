"""Texture serving for the 3D viewport.

The geometry binary buffer carries each textured primitive's `texture_file` as an
absolute path; the renderer fetches the actual image via
`GET /api/textures/serve?path=<path>` (3DWindow/api/endpoints.ts in the frontend).

We serve the file ONLY when its resolved path falls inside an allowlist of texture
directories — the committed default-texture picker set (`backend-api/assets`),
user uploads (`data_dir/uploads`), and the PyHelios plugin texture libraries — so
an arbitrary file on disk can never be read through this endpoint (path-traversal
safety).

GET /defaults lists the default textures the user can pick from (the images
committed in `backend-api/assets`).
"""
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.services.material_service import (
    default_textures_dir,
    get_texture_dirs,
    list_default_textures,
)

router = APIRouter()

# Same image formats the upload validator accepts (material_library_service).
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _allowed_texture_dirs() -> list[Path]:
    """Directories a texture may legitimately live in: the committed default
    textures (`backend-api/assets`), user-uploaded textures (`data_dir/uploads`)
    and the PyHelios plugin texture libraries — the latter also covers the
    default ground soil (plugins/visualizer/textures/dirt.jpg)."""
    dirs: list[Path] = [
        settings.data_dir / "uploads",                        # user-uploaded textures
        default_textures_dir(),                               # committed default textures (picker set)
    ]
    try:
        dirs.extend(tex_dir for _, tex_dir in get_texture_dirs())   # plugin libs incl. default soil
    except Exception:
        pass
    return dirs


def _is_within(target: Path, directory: Path) -> bool:
    """True when `target` (already resolved) sits inside `directory`."""
    try:
        target.relative_to(directory.resolve())
        return True
    except ValueError:
        return False


@router.get("/serve")
async def serve_texture(path: str) -> FileResponse:
    """Serve a texture image referenced by a primitive's texture path.

    Accepts both forms the app produces: the ABSOLUTE path baked into the
    geometry binary (viewport), and the RELATIVE `uploads/...` value stored on a
    material by the file-upload endpoint (material form / popup). A relative path
    is resolved against data_dir — resolving it against the process CWD instead
    would land outside the allowlist and 403 every uploaded texture.

    400 for a bad / non-image path, 403 when the path is outside the allowlist,
    404 when the file is missing.
    """
    try:
        raw = Path(path)
        resolved = (raw if raw.is_absolute() else settings.data_dir / raw).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(400, "Invalid texture path")

    if resolved.suffix.lower() not in _IMAGE_SUFFIXES:
        raise HTTPException(400, "Unsupported texture format")
    if not any(_is_within(resolved, d) for d in _allowed_texture_dirs()):
        raise HTTPException(403, "Texture path not allowed")
    if not resolved.is_file():
        raise HTTPException(404, "Texture file not found")
    return FileResponse(str(resolved))


@router.get("/defaults")
async def list_defaults() -> dict:
    """List the built-in default textures the user can pick from — the images
    committed in `backend-api/assets/`."""
    return {"textures": [
        {"name": t["name"],
         "url": f"/api/textures/serve?path={quote(t['path'])}"}
        for t in list_default_textures()
    ]}
