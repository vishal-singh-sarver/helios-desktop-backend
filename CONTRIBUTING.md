# Contributing to Helios Backend API

FastAPI backend for HeliosGUI. Owns the PyHelios 3D context, persists projects to SQLite, and exposes geometry/tree/plant-architecture APIs consumed by the Electron frontend over HTTP + SSE. This repo is a git submodule of [helios_gui](../) but has its own lifecycle.

## Stack

| Layer | Technology | Version |
|-------|-----------|---------|
| Web | FastAPI + Uvicorn | FastAPI 0.104+ |
| Validation | Pydantic v2 + pydantic-settings | 2.5+ |
| ORM | SQLAlchemy | 2.0+ |
| DB | SQLite | stdlib |
| 3D engine | PyHelios (C++ via pybind11) | submodule at `pyhelios/` |
| Numerics | NumPy | 1.26+ |
| Tests | pytest + httpx + pytest-anyio | pytest 9 |
| Lint/format | ruff | 0.4+ |
| Type check | mypy | 1.8+ |
| Python | 3.11+ | — |

## Project Map

```
app/
  core/           Config (pydantic-settings), logging, lifespan, timezone utility
  db/
    migrations/   Numbered SQL files (001_*, 002_*, ...) — run on startup in order
    database.py   Engine, session factory, migration runner
    models.py     ORM: Project, ProjectVersion, ProjectObject
  helios/
    context.py    PyHelios singleton (Context, WeberPennTree, PlantArchitecture)
    registry.py   In-memory object registry + cache
    persistence.py Save/load/version snapshots (gzip current, lzma archive)
  routers/        One file per API group — see API Reference below
  schemas/        Pydantic request/response models
  main.py         App factory, middleware, router registration
pyhelios/         PyHelios C++ source (git submodule — do not edit)
scripts/
  create_venv.sh    One-time venv + deps
  build_pyhelios.sh Build the native .so
  build_binary.sh   Package standalone executable (PyInstaller)
tests/            pytest suite
run.sh            Dev entrypoint (reads .env, starts uvicorn)
backend_wrapper.py PyInstaller entry for the packaged executable
```

## Commands

| Task | Command |
|------|---------|
| Set up venv + install | `bash scripts/create_venv.sh` |
| Activate venv | `source venv/bin/activate` |
| Dev server | `bash run.sh` |
| Build PyHelios native lib | `bash scripts/build_pyhelios.sh` |
| Tests | `python -m pytest tests/ -v` |
| Tests with coverage | `python -m pytest tests/ --cov=app` |
| Lint | `ruff check app/` |
| Format | `ruff format app/` |
| Type check | `mypy app/` |
| Package standalone binary | `bash scripts/build_binary.sh` |

Default port is `8008` (from [run.sh](run.sh)) unless `PORT` is set in `.env`. The README mentions `8000` — both exist in different entry points. Frontend expects whatever is in its `BASE_URL`.

## API Surface

| Prefix | Router | Purpose |
|--------|--------|---------|
| `/` | `system` | Health, version, `pyhelios_available` |
| `/api/project` | `project` | Create, save, load, version history, restore |
| `/api/geometry` | `geometry` / `transforms` | Primitives, binary buffers, transforms |
| `/api/objects` | `objects` | Per-object geometry + GPU child buffers |
| `/api/tree` | `tree` | Weber-Penn tree types & construction |
| `/api/plantarch` | `plantarch` | Plant architecture + SSE stream |
| `/api/materials` | `materials` | Create, assign, texture, color, two-sided |
| `/api/timeseries` | `timeseries` | Apply, list, delete time-series data |
| `/api` | `import_export` | Import OBJ / PLY |
| `/api/script` | `scripting` | Execute Python against the live PyHelios context |

Interactive docs at `/docs` when running.

## Conventions

- **Routers are thin.** An endpoint validates input via Pydantic, calls into `app/helios/` or `app/db/`, returns a schema. No business logic in the router body.
- **Pydantic v2 only.** Use `model_validate`, `model_dump`, `Field(...)`. Not `.dict()` / `.parse_obj()`.
- **SQLAlchemy 2.0 style.** `select(Model).where(...)` with a `Session`, not the legacy `Query` API. Use `session.scalars(...)` / `session.execute(...)`.
- **Migrations are append-only and auto-run.** To change schema, add a new `app/db/migrations/00N_description.sql` file. Never edit an existing migration — it will not re-run.
- **PyHelios is a singleton.** The context lives for the lifetime of the process. Mutations happen in-place; geometry endpoints return `503` when `pyhelios_available == False`.
- **Registry ↔ DB sync.** On every project save, the in-memory registry is mirrored to `project_objects`. Reads can pull from either; writes go through the registry.
- **Project persistence is two-tier.** Current snapshot: `data/projects/<id>/current.xml.gz` (fast). Version archive: `project_versions.scene_xml` BLOB, lzma-compressed.
- **SSE endpoints.** Plant architecture streams progress. Return an async generator with `media_type="text/event-stream"`; format events as `data: {json}\n\n`.
- **Errors.** Raise `HTTPException` with a structured detail (`{"error": "...", "code": "..."}`). Never leak raw exception strings or tracebacks in responses.
- **Tests.** One test file per router (`test_<router>.py`). Use `httpx.AsyncClient` + `pytest-anyio` for async endpoints. `tests/conftest.py` owns fixtures.

## NEVER

- Never edit `pyhelios/` from this repo — it is an external upstream submodule. Changes must go through the PyHelios project itself.
- Never commit `.env`, `data/heliosgui.db`, `data/projects/`, or any `*.xml.gz` / `*.xml.xz` (already gitignored — don't fight it).
- Never run SQL migrations manually or edit an existing migration file. Add `00N_new.sql` instead.
- Never expose `ipcRenderer`-style trust assumptions — the frontend is a separate process talking over HTTP. Validate every payload at the router boundary.
- Never return non-serializable objects from routers (numpy arrays, datetimes without tz, bytes without base64 encoding). Go through a Pydantic schema.
- Never call `context.build()` or other heavy PyHelios ops from a sync route handler on a hot path — offload to a background task or stream via SSE.
- Never hardcode file paths. Use `settings.DATA_DIR` or `app.getPath`-equivalent config from `app/core/config.py`.
- Never `shell=True` in subprocess calls. Never interpolate user input into shell commands.
- Never auto-commit or amend a pushed commit without explicit instruction.

## Cross-Repo Notes

- This repo's HEAD is tracked as a submodule pointer by the parent [helios_gui](../) repo. A commit here followed by a submodule-pointer bump in the parent is needed for the frontend to see backend changes.
- The frontend contract is in [../CONTRIBUTING.md](../CONTRIBUTING.md). Breaking an existing `/api/...` shape requires a coordinated frontend change — check `src/renderer/src/containers/*/saga.ts` in the parent repo before renaming or removing routes.

## Task Intake

For non-trivial changes, write up the task before starting work — a one-sentence goal, testable acceptance criteria, affected files, and constraints.
