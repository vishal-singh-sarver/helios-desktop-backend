import logging
import sys
from app.core.config import settings


def configure_logging() -> None:
    level = getattr(logging, settings.log_level.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Access logs follow the app's level instead of being pinned to WARNING,
    # which silenced them outright — `uvicorn --access-log` produced nothing,
    # so there was no record of which request did what. Diagnosing a duplicate
    # scenario load meant adding a temporary probe to the source, because the
    # server could not say which endpoint had asked for it.
    #
    # Set HELIOS_ACCESS_LOG=0 to get the old quiet behaviour back.
    if settings.access_log:
        logging.getLogger("uvicorn.access").setLevel(level)
    else:
        logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
