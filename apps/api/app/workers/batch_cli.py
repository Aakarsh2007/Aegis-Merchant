"""`python tasks.py batch` — put the corpus through the agent.

Runs against the configured database so the Command Center has something real
to show. Reproducible: the runner clears its own previous output first and
seeds its RNG, so running it twice produces the same numbers rather than
doubled ones.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.agent.nodes import AgentDeps
from app.config import get_settings
from app.core.clock import SystemClock
from app.db.session import create_engine, init_db
from app.llm.cache import CachedAdapter, ResponseCache
from app.workers.batch import run_batch


async def _main(limit: int | None) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_db(engine)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        clock = SystemClock()
        deps = AgentDeps(
            clock=clock,
            # The committed cache, not a live model: the batch must produce the
            # same numbers on a judge's machine with no API key.
            adapter=CachedAdapter(
                cache=ResponseCache.load(), live=None, model=settings.gemini_model
            ),
            control_arm_fraction=settings.control_arm_fraction,
            experiment_key="revpilot_recovery_v1",
        )
        result = await run_batch(factory, clock=clock, deps=deps, limit=limit)
    finally:
        await engine.dispose()

    print(result.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    limit = int(args[0]) if args and args[0].isdigit() else None
    return asyncio.run(_main(limit))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
