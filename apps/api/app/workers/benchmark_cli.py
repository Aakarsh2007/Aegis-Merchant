"""`python tasks.py benchmark` — the ablation table.

Reads the corpus, runs six decision policies over it, prints what each did.
No provider calls, no API key, no network. Deterministic: the same seed gives
the same table every time.
"""

from __future__ import annotations

import asyncio
import json
import sys

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.db.session import create_engine
from app.workers.benchmark import run_benchmark


async def _main(as_json: bool) -> int:
    settings = get_settings()
    engine = create_engine(settings.database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        report = await run_benchmark(factory)
    finally:
        await engine.dispose()

    print(json.dumps(report.as_dict(), indent=2) if as_json else report.render())
    return 0


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    try:
        return asyncio.run(_main("--json" in args))
    except RuntimeError as exc:
        print(f"benchmark did not run: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
