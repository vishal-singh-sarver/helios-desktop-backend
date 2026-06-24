"""Texture serving for the 3D viewport.

The geometry binary buffer carries each textured primitive's `texture_file` as an
absolute path; the renderer fetches the actual image via
`GET /api/textures/serve?path=<path>` (3DWindow/api/endpoints.ts in the frontend).

We serve the file ONLY when its resolved path falls inside an allowlist of texture
directories — the bundled default-ground texture (`app/assets`), user uploads
(`data_dir/uploads`), and the PyHelios plugin texture libraries — so an arbitrary
file on disk can never be read through this endpoint (path-traversal safety).
"""
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.core.config import settings
from app.services.material_service import get_texture_dirs

router = APIRouter()

# Same image formats the upload validator accepts (material_library_service).
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg"}


def _allowed_texture_dirs() -> list[Path]:
    """Directories a texture may legitimately live in: user-uploaded textures
    (`data_dir/uploads`) and the PyHelios plugin texture libraries — the latter
    also covers the default ground soil (plugins/visualizer/textures/dirt.jpg)."""
    dirs: list[Path] = [
        settings.data_dir / "uploads",                        # user-uploaded textures
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

    400 for a bad / non-image path, 403 when the path is outside the allowlist,
    404 when the file is missing.
    """
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError, RuntimeError):
        raise HTTPException(400, "Invalid texture path")

    if resolved.suffix.lower() not in _IMAGE_SUFFIXES:
        raise HTTPException(400, "Unsupported texture format")
    if not any(_is_within(resolved, d) for d in _allowed_texture_dirs()):
        raise HTTPException(403, "Texture path not allowed")
    if not resolved.is_file():
        raise HTTPException(404, "Texture file not found")
    return FileResponse(str(resolved))
