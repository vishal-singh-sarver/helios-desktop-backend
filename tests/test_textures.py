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


# ── Default-texture picker: GET /api/textures/defaults ───────────────────────


def test_defaults_lists_seeded_dirt(client):
    """Startup seeds data/assets with the ground default; /defaults lists it
    with a name and a /serve URL."""
    r = client.get("/api/textures/defaults")
    assert r.status_code == 200, r.text
    entries = r.json()["textures"]
    dirt = next((t for t in entries if t["name"] == "dirt.jpg"), None)
    assert dirt is not None, entries
    assert dirt["url"].startswith("/api/textures/serve?path=")


def test_defaults_url_serves_the_image(client):
    """A /defaults entry's URL is directly servable (proves data/assets is on
    the serve allowlist)."""
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


def test_defaults_seeded_into_data_assets_and_idempotent(client):
    """The default lands physically in data/assets, and re-seeding is a no-op
    (skips the already-present file rather than re-copying or raising)."""
    from app.services import material_service
    dirt = material_service.default_textures_dir() / "dirt.jpg"
    assert dirt.is_file()
    mtime = dirt.stat().st_mtime
    material_service.seed_default_textures()          # run again
    assert dirt.stat().st_mtime == mtime              # untouched — existing file kept


def test_ground_default_unchanged_still_from_submodule(client):
    """Existing functionality untouched: the ground default still resolves from
    the pyhelios submodule, NOT the new data/assets picker folder — we only
    copied a second instance for the picker."""
    src = material_apply._DEFAULT_GROUND_TEXTURE
    assert src and "pyhelios" in src                  # still the submodule copy
    assert str(settings.data_dir) not in src          # not repointed to data/assets
