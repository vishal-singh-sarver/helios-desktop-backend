"""
SQLite database connection and session management.

Usage in endpoints (dependency injection):
    from app.db.database import get_db
    from sqlalchemy.orm import Session

    @router.get("/example")
    def example(db: Session = Depends(get_db)):
        ...
"""
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from app.core.config import settings

# SQLite URL — file-based, one DB per installation
_db_url = f"sqlite:///{settings.resolved_db_path}"

engine = create_engine(
    _db_url,
    echo=settings.db_echo,
    connect_args={"check_same_thread": False},  # required for SQLite + FastAPI
)

# Enable WAL mode for better concurrent read performance
@event.listens_for(engine, "connect")
def set_sqlite_pragma(dbapi_conn, _):
    cursor = dbapi_conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """FastAPI dependency — yields a DB session, closes on exit."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_migrations() -> None:
    """
    Apply all SQL migration scripts in db/migrations/ in order.
    Called once at startup from lifespan.py.
    """
    # TODO: implement in Step 2
    pass
