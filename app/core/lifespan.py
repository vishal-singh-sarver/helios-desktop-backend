from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.core.config import settings
from app.core.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Runs once on startup (before yield) and once on shutdown (after yield).

    Startup order:
      1. Logging
      2. Ensure data directories exist
      3. Database — run migrations
      4. PyHelios — validate library availability

    Add new startup steps here; keep each concern in its own module.
    """
    # 1. Logging
    configure_logging()

    # 2. Data directories
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_projects_dir.mkdir(parents=True, exist_ok=True)

    # 3. Database migrations
    from app.db.database import run_migrations
    run_migrations()

    # 4. PyHelios availability check — TODO: implement in Step 3
    # from app.helios.context import init_pyhelios
    # init_pyhelios()

    yield

    # Shutdown — clean up resources
    # from app.helios.context import shutdown_pyhelios
    # shutdown_pyhelios()
