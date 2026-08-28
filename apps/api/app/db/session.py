"""Async engine and session factory.

The pragmas below are not optional decoration — each one prevents a specific
failure this system would otherwise hit:

``journal_mode=WAL``
    Lets the API read while a background worker writes. Without it the
    dashboard blocks whenever the outbox drainer holds the write lock.

``foreign_keys=ON``
    SQLite enforces foreign keys **only** if you ask, per connection. Off by
    default, silently. Every ``ondelete`` clause in models.py is inert without
    this, which is exactly the kind of constraint that looks present in the
    schema and does nothing.

``busy_timeout=5000``
    WAL permits one writer. Concurrent writers get ``database is locked``
    immediately unless told to wait (failure matrix #15).

``synchronous=NORMAL``
    Survives process crash — which is the case the outbox reconciler exists
    for — while avoiding an fsync per commit. Only OS-level or power loss can
    lose the last transaction, and that is the right trade here.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import ConnectionPoolEntry

from app.config import get_settings
from app.db.base import Base
from app.db.models import ALL_MODELS  # noqa: F401  (import registers every table)

__all__ = [
    "PRAGMAS",
    "create_engine",
    "drop_all",
    "get_engine",
    "get_sessionmaker",
    "init_db",
    "read_pragmas",
    "session_scope",
]

#: Applied to every new connection. Asserted in tests by querying them back —
#: setting a pragma and assuming it took is how WAL silently stays off.
PRAGMAS: dict[str, str] = {
    "journal_mode": "WAL",
    "foreign_keys": "ON",
    "busy_timeout": "5000",
    "synchronous": "NORMAL",
}

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _register_pragmas(engine: AsyncEngine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
        cursor = dbapi_conn.cursor()
        try:
            for name, value in PRAGMAS.items():
                cursor.execute(f"PRAGMA {name}={value}")
        finally:
            cursor.close()


def create_engine(url: str | None = None, *, echo: bool = False) -> AsyncEngine:
    """Build an engine with the pragmas wired in."""
    settings = get_settings()
    engine = create_async_engine(
        url or settings.database_url,
        echo=echo,
        future=True,
        # SQLite writes serialise anyway; a large pool buys contention, not
        # throughput.
        pool_pre_ping=True,
    )
    _register_pragmas(engine)
    return engine


def get_engine() -> AsyncEngine:
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    global _sessionmaker
    if _sessionmaker is None:
        _sessionmaker = async_sessionmaker(
            get_engine(),
            expire_on_commit=False,  # objects stay usable after commit
            autoflush=False,  # flushes are explicit; ordering matters here
        )
    return _sessionmaker


async def session_scope() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency. Commits on success, rolls back on any exception."""
    factory = get_sessionmaker()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def read_pragmas(engine: AsyncEngine) -> dict[str, Any]:
    """Read the pragmas back from a live connection.

    Exists so the test suite can *prove* WAL and foreign-key enforcement are
    on, rather than trusting that the connect hook ran.
    """
    out: dict[str, Any] = {}
    async with engine.connect() as conn:
        for name in ("journal_mode", "foreign_keys", "busy_timeout", "synchronous"):
            result = await conn.execute(text(f"PRAGMA {name}"))
            row = result.first()
            out[name] = row[0] if row else None
    return out


async def init_db(engine: AsyncEngine | None = None) -> None:
    """Create every table.

    ``create_all`` rather than a migration tool: there is no deployed instance
    to migrate, the seed script is the schema fixture, and during the build a
    schema change means deleting the dev database and re-seeding in under a
    second (workflow.md ADL-012, docs/DECISIONS.md DEC-006).
    """
    target = engine or get_engine()
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def drop_all(engine: AsyncEngine | None = None) -> None:
    target = engine or get_engine()
    async with target.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


async def dispose() -> None:
    """Close pooled connections. Called on shutdown and between tests."""
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
