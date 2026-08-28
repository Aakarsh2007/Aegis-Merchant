"""Shared fixtures.

Every database test gets its own on-disk temporary database rather than
``:memory:``. Two reasons: WAL mode does not apply to in-memory databases, so
an in-memory test could not verify the pragma that most of the concurrency
design depends on; and the outbox reconciler's whole purpose is surviving a
process restart, which needs a real file.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.core.clock import FakeClock
from app.db.session import create_engine, init_db


@pytest.fixture
def clock() -> FakeClock:
    """A frozen clock at a quiet, unremarkable weekday morning IST."""
    return FakeClock.at_ist(2026, 9, 1, 11, 30)


@pytest_asyncio.fixture
async def engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    db = tmp_path / "test.db"
    eng = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    await init_db(eng)
    try:
        yield eng
    finally:
        await eng.dispose()


@pytest_asyncio.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    async with factory() as s:
        yield s


@pytest_asyncio.fixture
async def seeded_engine(tmp_path: Path) -> AsyncIterator[AsyncEngine]:
    """A fully seeded 420-transaction corpus."""
    from app.db.seed import seed_to_engine

    db = tmp_path / "seeded.db"
    eng = create_engine(f"sqlite+aiosqlite:///{db.as_posix()}")
    await seed_to_engine(eng)
    try:
        yield eng
    finally:
        await eng.dispose()
