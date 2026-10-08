
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import sessionmaker, DeclarativeBase
from sqlalchemy.pool import QueuePool, NullPool
from .config import settings


def _create_engine() -> Engine:
    """Build the engine with SQLite-simple or PostgreSQL-bounded pooling."""
    url = settings.database_url
    if url.startswith("sqlite"):
        # SQLite-specific simple engine: one writer at a time, no pool.
        return create_engine(
            url,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False},
        )

    # PostgreSQL: psycopg3 driver, bounded (queued) pool, connection timeout,
    # pool recycle, and pre-ping to detect stale connections.
    return create_engine(
        url,
        future=True,
        poolclass=QueuePool,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        connect_args={"connect_timeout": settings.db_connect_timeout},
    )


engine: Engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

