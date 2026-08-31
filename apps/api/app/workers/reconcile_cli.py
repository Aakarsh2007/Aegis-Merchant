"""`python tasks.py reconcile` — ask Razorpay what was actually paid.

The command that makes the Test Mode demo tunnel-free: create a link, pay it,
run this. No public URL, no webhook registration, nothing to re-register before
a recording.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.clock import SystemClock
from app.db.session import create_engine
from app.deps import get_provider
from app.workers.reconcile import reconcile_outstanding


async def _main() -> int:
    settings = get_settings()
    if not settings.razorpay_live:
        print(
            "! No Razorpay credentials, so there is nothing to reconcile against.\n"
            "  Set RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env (Test Mode is free).",
            file=sys.stderr,
        )
        return 2

    engine = create_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        result = await reconcile_outstanding(
            factory, provider=get_provider(settings), clock=SystemClock()
        )
    finally:
        await engine.dispose()

    print(result.render())
    # Non-zero only on a real failure. "Nothing was paid yet" is a normal
    # answer, not an error, and returning 1 for it would break any script that
    # polls this on a timer.
    return 1 if result.errors and not result.settled else 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main())


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
