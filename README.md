# Helios Backend API

FastAPI backend for HeliosGUI. Manages 3D scene state via PyHelios, persists projects to SQLite, and streams geometry to the frontend over a REST API.

## Requirements

- Python 3.11+
- CMake 3.20+ and a C++17 compiler (for PyHelios source build)

## Setup

```bash
# 1. Create virtualenv and install dependencies
bash scripts/create_venv.sh

# 2. Copy and edit environment config
cp .env.example .env

# 3. Init the PyHelios submodule
git submodule update --init --recursive

# 4. Build PyHelios native library (optional — skipped if using pip wheel)
bash scripts/build_pyhelios.sh

# 5. Start the server
bash run.sh
```

The server starts at `http://127.0.0.1:8000` by default.

## Environment Variables

See [.env.example](.env.example) for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | `development` or `production` |
| `HOST` | `127.0.0.1` | Bind address |
| `PORT` | `8000` | Bind port |
| `RELOAD` | `true` | Uvicorn hot-reload (dev only) |
| `DATA_DIR` | `data` | Root for DB and project files |
| `PYHELIOS_USE_PIP` | `false` | Use pip wheel instead of source submodule |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated allowed origins |

## Project Structure

```
helios-backend/
├── app/
│   ├── core/           # Config, logging, lifespan, timezone utility
│   ├── db/
│   │   ├── migrations/ # Numbered SQL migration files (001_, 002_, ...)
│   │   ├── database.py # SQLAlchemy engine, session, migration runner
│   │   └── models.py   # ORM models (Project, ProjectVersion, ProjectObject)
│   ├── helios/
│   │   ├── context.py  # PyHelios singleton (Context, WeberPennTree, PlantArchitecture)
│   │   ├── registry.py # In-memory object registry with cache management
│   │   └── persistence.py # Save/load/version snapshots (gzip + lzma)
│   ├── routers/        # One file per API group (see API Reference below)
│   ├── schemas/        # Pydantic request/response models
│   └── main.py         # App factory, middleware, router registration
├── pyhelios/           # PyHelios git submodule
├── scripts/
│   ├── create_venv.sh
│   └── build_pyhelios.sh
├── tests/
├── run.sh
├── requirements.txt
└── requirements-dev.txt
```

## Database

SQLite at `data/heliosgui.db`. Migrations run automatically on startup from `app/db/migrations/` in version order.

### Schema

**`projects`** — one row per project
**`project_versions`** — lzma-compressed Helios XML snapshots with registry JSON
**`project_objects`** — flat mirror of the in-memory object registry, synced on every save

## PyHelios

By default the source submodule at `pyhelios/` is used. The app detects staleness (source files newer than the built `.so`) and triggers a rebuild automatically on startup.

To use a pip-installed wheel instead, set `PYHELIOS_USE_PIP=true` in `.env`.

If PyHelios is unavailable all geometry endpoints return `503`. The `/health` endpoint reports availability via `pyhelios_available`.

## API Reference

| Prefix | Router | Description |
|---|---|---|
| `/` | `system` | Health, version, PyHelios info |
| `/api/project` | `project` | Create, save, load, version history, restore |
| `/api/geometry` | `geometry` | Add primitives, query geometry, bulk binary/GPU buffers |
| `/api/geometry` | `transforms` | Centroid, translate, rotate, scale |
| `/api/objects` | `objects` | Per-object geometry and GPU child buffers |
| `/api/tree` | `tree` | Weber-Penn tree types and construction |
| `/api/plantarch` | `plantarch` | Plant architecture species, canopy, SSE stream |
| `/api/materials` | `materials` | Create, assign, texture, color, two-sided flag |
| `/api/timeseries` | `timeseries` | Apply, list, delete time-series data |
| `/api` | `import_export` | Import OBJ and PLY files |
| `/api/script` | `scripting` | Execute Python snippets against the live context |

Interactive docs are available at `http://127.0.0.1:8000/docs` when the server is running.

## Development

```bash
source venv/bin/activate

# Run tests
python -m pytest tests/ -v

# Lint
ruff check app/

# Type check
mypy app/
```

## Persistence Model

Projects are stored in two layers:

- **Current snapshot** — `data/projects/<id>/current.xml.gz` (gzip, fast read/write)
- **Version archive** — `project_versions.scene_xml` BLOB in SQLite (lzma, ~85-90% compression)

UTC offset is calculated from project coordinates at creation time using `timezonefinder` and stored in the `projects` table.

#test
