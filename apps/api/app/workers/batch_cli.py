"""`python tasks.py batch` — put the corpus through the agent.

Runs against the configured database so the Command Center has something real
to show. Reproducible: the runner clears its own previous output first and
seeds its RNG, so running it twice produces the same numbers rather than
doubled ones.

``--warm`` is the same run with a live model behind the cache, recording every
response. It exists because of INC-029: ``warm-cache`` warmed the cache from
the golden eval set, whose context has five keys, while the agent sends eight
(it adds LTV, prior orders and amount). The cache key is a hash of the whole
context, so not one of the 81 committed entries could ever match a lookup the
batch makes. The committed cache had a structurally guaranteed 0% hit rate in
the demo it exists to serve, and every model consultation fell through to the
deterministic floor.

Warming by *running the batch itself* is the fix that cannot drift: the
contexts are the batch's own, so a key recorded here is by construction the key
looked up later. Reconstructing contexts in a second place is what broke.
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
from app.llm.gemini_adapter import GeminiAdapter
from app.llm.rate_limit import RateLimiter
from app.workers.batch import EmptyCorpusError, run_batch


async def _main(limit: int | None, warm: bool = False) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_db(engine)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        clock = SystemClock()
        live = None
        if warm:
            if not settings.gemini_api_key:
                print("--warm needs GEMINI_API_KEY. Nothing recorded.", file=sys.stderr)
                return 2
            live = GeminiAdapter(
                settings.gemini_api_key,
                clock=clock,
                model=settings.gemini_model,
                timeout_s=settings.llm_timeout_s,
                max_output_tokens=settings.llm_max_output_tokens,
                # Wait for an RPM slot instead of answering deterministically.
                # Without this the limiter refuses under load and the adapter
                # falls through to its floor -- so a warming run records only
                # the first ~10 calls of each minute and quietly leaves the rest
                # uncached. The first full warm attempt recorded 17 of 226 that
                # way. `warm_cache.py` already passed this; `--warm` did not.
                wait_for_slot_s=90.0,
                rate_limiter=RateLimiter(
                    clock=clock,
                    rpm_limit=settings.llm_rpm_limit,
                    rpd_limit=settings.llm_rpd_limit,
                ),
            )
        adapter = CachedAdapter(
            cache=ResponseCache.load(),
            # Normally None: the batch must produce the same numbers on a
            # judge's machine with no API key. `--warm` is the offline pass
            # that fills the cache those judges will then hit.
            live=live,
            model=settings.gemini_model,
            record=warm,
        )
        deps = AgentDeps(
            clock=clock,
            adapter=adapter,
            control_arm_fraction=settings.control_arm_fraction,
            experiment_key="revpilot_recovery_v1",
        )
        try:
            result = await run_batch(factory, clock=clock, deps=deps, limit=limit)
        except EmptyCorpusError as empty:
            # A clean clone where `batch` was run before `demo`. Printing the
            # fix beats a traceback, and beats the old behaviour of reporting
            # "BATCH COMPLETE -- 0 cases" and exiting zero (INC-046).
            print()
            print(f"  {empty}")
            print()
            return 1
        if warm:
            # Saved even on a partial run. A quota ceiling mid-way leaves the
            # cache with fewer entries, not with none: the remaining
            # consultations fall through to the deterministic floor and are
            # labelled that way, which is a smaller hit rate rather than a lie.
            adapter.save()
            print(
                f"cache: {len(adapter.cache)} entries "
                f"({adapter.hits} hits, {adapter.misses} misses this run)"
            )
    finally:
        await engine.dispose()

    print(result.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    warm = "--warm" in args
    positional = [a for a in args if a.isdigit()]
    limit = int(positional[0]) if positional else None
    return asyncio.run(_main(limit, warm=warm))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
