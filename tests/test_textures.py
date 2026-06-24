"""Texture serving endpoint (GET /api/textures/serve) — the 3D viewport fetches
each primitive's texture image through it."""
from app.core.config import settings
from app.services import material_apply


def test_serve_default_ground_texture(client):
    """The bundled default soil (app/assets) is served as an image."""
    r = client.get("/api/textures/serve",
                   params={"path": material_apply._DEFAULT_GROUND_TEXTURE})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 1000          # a real jpeg, not an error body


def test_serve_rejects_non_image(client):
    r = client.get("/api/textures/serve", params={"path": "/etc/passwd"})
    assert r.status_code == 400           # no image suffix


def test_serve_rejects_path_outside_allowlist(client):
    """An image path outside app/assets / uploads / plugin dirs is forbidden
    (path-traversal safety) — even before checking existence."""
    r = client.get("/api/textures/serve", params={"path": "/tmp/evil.png"})
    assert r.status_code == 403


def test_serve_404_for_missing_file_in_allowed_dir(client):
    missing = str(settings.data_dir / "uploads" / "nope_missing.png")
    r = client.get("/api/textures/serve", params={"path": missing})
    assert r.status_code == 404
