"""Texture serving endpoint (GET /api/textures/serve) — the 3D viewport fetches
each primitive's texture image through it."""
from pathlib import Path

from app.core.config import settings
from app.services import material_apply


def test_serve_default_ground_texture(client):
    """The bundled default soil (pyhelios plugin dir) is served as an image."""
    r = client.get("/api/textures/serve",
                   params={"path": material_apply._DEFAULT_GROUND_TEXTURE})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 1000          # a real jpeg, not an error body


def test_serve_rejects_non_image(client):
    r = client.get("/api/textures/serve", params={"path": "/etc/passwd"})
    assert r.status_code == 400           # no image suffix


def test_serve_rejects_path_outside_allowlist(client):
    """An image path outside backend-api/assets / uploads / plugin dirs is
    forbidden (path-traversal safety) — even before checking existence."""
    r = client.get("/api/textures/serve", params={"path": "/tmp/evil.png"})
    assert r.status_code == 403


def test_serve_404_for_missing_file_in_allowed_dir(client):
    missing = str(settings.data_dir / "uploads" / "nope_missing.png")
    r = client.get("/api/textures/serve", params={"path": missing})
    assert r.status_code == 404


def test_serve_uploaded_texture_both_path_forms(client):
    """An uploaded texture must serve from BOTH forms the app produces:

      * the stored value `uploads/...`  — sent by the material form / popup
      * resolve_texture_path(value)     — baked into the geometry binary and
                                          sent by the 3D viewport

    data_dir defaults to the RELATIVE Path("data"), so these two differ: the
    baked form must be absolute (else serve re-applies the data_dir prefix and
    403s the viewport), and the bare stored form must resolve against data_dir
    (else it resolves against the CWD and 403s the popup)."""
    import io
    from uuid import uuid4

    h = {"session-id": f"session_{uuid4().hex[:8]}"}
    client.post("/api/project/create", json={
        "name": f"Tex_{uuid4().hex[:8]}", "latitude": 28.6, "longitude": 77.2,
    }, headers=h)
    vis = next(mt["id"] for mt in client.get("/api/catalog/material-types").json()
               ["material_types"] if mt["materialtype"] == "Visualiser")
    grp = client.post("/api/materials/library/groups",
                      json={"materials": []}, headers=h).json()["group"]

    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    up = client.post(
        f"/api/materials/library/groups/{grp['id']}/materials/{vis}/files/texture_file",
        files={"file": ("grass.png", io.BytesIO(png), "image/png")}, headers=h)
    assert up.status_code == 200, up.text
    stored = up.json()["value"]
    assert not stored.startswith("/")          # the stored value IS relative

    # The baked/viewport form is absolute, so serve never re-prefixes it.
    baked = material_apply.resolve_texture_path(stored)
    assert Path(baked).is_absolute(), baked

    for form in (stored, baked):
        r = client.get("/api/textures/serve", params={"path": form})
        assert r.status_code == 200, f"{form} -> {r.status_code} {r.text}"
        assert r.content == png


# ── Default-texture picker: GET /api/textures/defaults ───────────────────────


def test_defaults_lists_committed_dirt(client):
    """The committed default (backend-api/assets/dirt.jpg) is listed by /defaults
    with a name and a /serve URL."""
    r = client.get("/api/textures/defaults")
    assert r.status_code == 200, r.text
    entries = r.json()["textures"]
    dirt = next((t for t in entries if t["name"] == "dirt.jpg"), None)
    assert dirt is not None, entries
    assert dirt["url"].startswith("/api/textures/serve?path=")


def test_defaults_url_serves_the_image(client):
    """A /defaults entry's URL is directly servable (proves backend-api/assets is
    on the serve allowlist)."""
    entries = client.get("/api/textures/defaults").json()["textures"]
    entry = next(t for t in entries if t["name"] == "dirt.jpg")   # not [0] — order-independent
    r = client.get(entry["url"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("image/")
    assert len(r.content) > 1000


def test_list_default_textures_filters_and_empty(tmp_path, monkeypatch):
    """Unit: absent dir -> []; non-images, subdirs, and excluded logos are
    skipped; only real image files are listed."""
    from app.services import material_service

    # Absent folder → empty list, no error (e.g. seed failed / pyhelios absent).
    monkeypatch.setattr(material_service, "default_textures_dir",
                        lambda: tmp_path / "nope")
    assert material_service.list_default_textures() == []

    # Mixed folder: only the image survives the filters.
    d = tmp_path / "assets"
    d.mkdir()
    (d / "grass.png").write_bytes(b"x")
    (d / "notes.txt").write_bytes(b"x")          # non-image
    (d / "USDA_logo.jpg").write_bytes(b"x")      # excluded logo
    (d / "nav_gizmo_x.png").write_bytes(b"x")    # excluded prefix
    (d / "sub").mkdir()                          # subdir, not a file
    (d / "sub" / "deep.jpg").write_bytes(b"x")
    monkeypatch.setattr(material_service, "default_textures_dir", lambda: d)
    assert [t["name"] for t in material_service.list_default_textures()] == ["grass.png"]


def test_default_texture_committed_in_assets(client):
    """The default texture ships as a committed file in backend-api/assets —
    no runtime hydration, no copy on startup."""
    from app.services import material_service
    dirt = material_service.default_textures_dir() / "dirt.jpg"
    assert dirt.is_file()


def test_ground_default_unchanged_still_from_submodule(client):
    """Existing functionality untouched: the ground default still resolves from
    the pyhelios submodule, NOT the picker's backend-api/assets folder — the
    picker keeps its own committed copy."""
    src = material_apply._DEFAULT_GROUND_TEXTURE
    assert src and "pyhelios" in src                  # still the submodule copy
    from app.services.material_service import default_textures_dir
    assert str(default_textures_dir()) not in src     # not repointed to the picker dir
