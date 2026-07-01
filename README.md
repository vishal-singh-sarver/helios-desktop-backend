# Helios Backend API

FastAPI backend for HeliosGUI. Manages 3D scene state via PyHelios, persists projects to SQLite, and streams geometry to the frontend over a REST API.

## Requirements

- Python 3.10+
- CMake 3.15+ and a C++17 compiler (for PyHelios source build)

## Setup

```bash
# 1. Create virtualenv and install dependencies (incl. dev tools)
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

With the provided `.env`, the server starts at `http://127.0.0.1:8000`. Without a `.env`, `run.sh` falls back to its own defaults and binds `http://0.0.0.0:8008`.

## Environment Variables

See [.env.example](.env.example) for the full list. Key variables:

| Variable | Default | Description |
|---|---|---|
| `APP_ENV` | `development` | `development` \| `staging` \| `production` |
| `HOST` | `127.0.0.1` | Bind address (config default; `run.sh` falls back to `0.0.0.0`) |
| `PORT` | `8000` | Bind port (config default; `run.sh` falls back to `8008`) |
| `RELOAD` | `false` | Uvicorn hot-reload (dev only); `.env.example` ships `true` |
| `LOG_LEVEL` | `INFO` | Logging level |
| `DATA_DIR` | `data` | Root for DB and project files (also read from `HELIOS_DATA_DIR`) |
| `DB_ECHO` | `false` | Echo SQL statements to the log |
| `PYHELIOS_USE_PIP` | `false` | Use pip wheel instead of source submodule |
| `SECRET_KEY` | `change-me` | Session/crypto secret — override in production |
| `CORS_ORIGINS` | `http://localhost:5173,...` | Comma-separated allowed origins |

## Project Structure

```
backend-api/
├── app/
│   ├── core/           # Config, logging, lifespan, dependencies, project/scenario context, session store, timezone
│   ├── db/
│   │   ├── migrations/ # Numbered SQL migration files (001_ … 016_), auto-run on startup
│   │   ├── database.py # SQLAlchemy engine, session, migration runner
│   │   └── models.py   # ORM models (Project, ProjectVersion, Scenario, ProjectObject, HeliosDataType, DataUnit, WeatherDataHeader)
│   ├── helios/
│   │   ├── context.py  # PyHelios singleton (Context, WeberPennTree, PlantArchitecture)
│   │   ├── registry.py # In-memory object registry with cache management
│   │   └── persistence.py # Save/load context snapshots + versioned archives
│   ├── routers/        # One file per API group (see API Reference below)
│   ├── services/       # Business logic (per-feature *_service.py) called by routers
│   ├── schemas/        # Pydantic request/response models
│   └── main.py         # App factory, middleware, router registration
├── pyhelios/           # PyHelios git submodule
├── scripts/
│   ├── create_venv.sh      # venv + dev dependencies
│   ├── build_pyhelios.sh   # build the native PyHelios lib (build_pyhelios.ps1 on Windows)
│   └── build_binary.sh     # PyInstaller standalone bundle (build_binary.ps1 on Windows)
├── tests/
├── backend_wrapper.py  # PyInstaller entry for the packaged binary
├── run.sh
├── .env.example
├── requirements.txt
└── requirements-dev.txt
```

## Database

SQLite at `data/heliosgui.db` (override via `DATA_DIR`/`HELIOS_DATA_DIR` or `DB_PATH`). Migrations run automatically on startup from `app/db/migrations/` in version order (currently `001`–`016`).

### Schema

Core tables created by the migrations:

- **`projects`** — one row per project
- **`project_versions`** — lzma-compressed Helios XML snapshots with registry JSON
- **`project_objects`** — flat mirror of the in-memory object registry, synced on every save
- **`scenarios`** — per-project scenarios
- **`weather_data_headers`** — per-scenario weather/time-series column metadata
- **`helios_data_types`**, **`data_units`** — catalog tables for Helios data types and units

## PyHelios

By default the source submodule at `pyhelios/` is used. The app detects staleness (source files newer than the built `.so`) and triggers a rebuild automatically on startup.

To use a pip-installed wheel instead, set `PYHELIOS_USE_PIP=true` in `.env`.

If PyHelios is unavailable, the tree, plantarch, and scripting endpoints return `503` (geometry endpoints surface the error as `500`). The `/health` endpoint reports availability via `pyhelios_available`.

## API Reference

| Prefix | Router | Description |
|---|---|---|
| `/` | `system` | Health, version, PyHelios info |
| `/api/project` | `project` | Create, save, load, version history, restore |
| `/api/project` | `scenario` | Create, list, delete per-project scenarios |
| `/api/geometry` | `geometry` | Add primitives, query geometry, bulk binary/GPU buffers |
| `/api/geometry` | `transforms` | Centroid, translate, rotate, scale |
| `/api/objects` | `objects` | Per-object geometry and GPU child buffers |
| `/api/tree` | `tree` | Weber-Penn tree types and construction |
| `/api/plantarch` | `plantarch` | Plant architecture species, canopy, SSE stream |
| `/api/materials` | `materials` | Create, assign, texture, color, two-sided flag |
| `/api/timeseries` | `timeseries` | Apply, list, delete time-series data |
| `/api/weather` | `weather` | Per-scenario weather/time-series table: upload, columns, rows, header CRUD |
| `/api` | `import_export` | Import OBJ and PLY files |
| `/api/script` | `scripting` | Execute Python snippets against the live context |
| `/api/data-types` | `helios_data_type` | Catalog: CRUD for Helios data types |
| `/api/data-units` | `data_unit` | Catalog: CRUD for data units |

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

- **Current snapshot** — `data/projects/<project_id>/scenarios/<scenario_id>/context_file/context.xml` (plain, uncompressed XML). Rotated backups are gzipped into `context_file/archives/autosave_<timestamp>.xml.gz`.
- **Version archive** — `project_versions.scene_xml` BLOB in SQLite (lzma, ~85-90% compression)

UTC offset is calculated from project coordinates at creation time using `timezonefinder` and stored in the `projects` table.

#test
