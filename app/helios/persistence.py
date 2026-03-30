"""
Context persistence — save/load/version.

Storage layout:
  data/projects/{project_id}/
      current.xml.gz      ← gzip snapshot of live context (fast r/w)
      registry.json       ← _object_registry + project metadata

  SQLite: project_versions table
      scene_xml BLOB      ← lzma-compressed XML (archived versions)
      registry_json TEXT  ← registry at that version

Compression tiers:
  gzip  (stdlib) — current working file. Fast, ~70% reduction.
  lzma  (stdlib) — archived versions.   Slower, ~85-90% reduction.
"""
# TODO: implement in Step 3


def save_snapshot(project_id: str, ctx, registry: dict, metadata: dict) -> None:
    """
    Write current context to disk:
      1. ctx.writeXML(tmp) → gzip → current.xml.gz
      2. registry.json
      3. lzma compress → INSERT into project_versions
    """
    raise NotImplementedError("Implement in Step 3")


def load_snapshot(project_id: str, ctx) -> dict:
    """
    Restore context from disk:
      1. Decompress current.xml.gz
      2. ctx.loadXML(tmp)
      3. Return registry dict
    """
    raise NotImplementedError("Implement in Step 3")


def save_version(project_id: str, label: str, db) -> int:
    """Compress current snapshot with lzma and insert a new version row. Returns version id."""
    raise NotImplementedError("Implement in Step 3")


def restore_version(project_id: str, version_id: int, ctx, db) -> dict:
    """Load a specific archived version into the context."""
    raise NotImplementedError("Implement in Step 3")


def list_versions(project_id: str, db) -> list:
    """Return all version rows for a project (without the blob)."""
    raise NotImplementedError("Implement in Step 3")
