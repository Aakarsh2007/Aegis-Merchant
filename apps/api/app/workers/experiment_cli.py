"""`python tasks.py testmode-experiment [n]` — a real randomised holdout.

Creates real Razorpay Test Mode payment links for the treated arm and none for
the control arm. Needs `RAZORPAY_KEY_ID` and `RAZORPAY_KEY_SECRET`, and a
running webhook tunnel if you want the settlements to arrive.

Deliberately not part of `tasks.py demo`. It makes live provider calls, and a
judge running the demo should get the offline, reproducible path.
"""

from __future__ import annotations

import asyncio
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.clock import SystemClock
from app.db.session import create_engine, init_db
from app.workers.experiment import run_testmode_experiment


async def _main(n: int) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    await init_db(engine)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
        result = await run_testmode_experiment(factory, clock=SystemClock(), settings=settings, n=n)
    finally:
        await engine.dispose()
    print(result.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    n = int(args[0]) if args and args[0].isdigit() else 10
    try:
        return asyncio.run(_main(n))
    except (RuntimeError, ValueError) as exc:
        print(f"experiment did not run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
