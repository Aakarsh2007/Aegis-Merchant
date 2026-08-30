"""Verify the audit chain from the command line (§13.4).

The same verifier the endpoint uses, reachable without starting the API. That
matters for the demo: a judge can verify the committed database with the server
stopped, which removes "the running process is lying to you" as an explanation.

Exit code is 0 for a valid chain and 1 for a broken one, so it works in CI and
in a shell pipeline without parsing the output.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import get_settings
from app.core.clock import SystemClock
from app.db.session import create_engine
from app.tools.audit import AuditChain


async def _verify(database_url: str) -> int:
    engine = create_engine(database_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as session:
            result = await AuditChain(SystemClock()).verify(session)
    finally:
        await engine.dispose()

    print(f"database : {database_url}")
    print(f"blocks   : {result.blocks}")
    print(f"valid    : {result.valid}")
    if result.head_hash:
        print(f"head     : {result.head_hash}")
    if result.first_divergence_index is not None:
        print(f"diverges : block {result.first_divergence_index}")
    if result.reason:
        print(f"note     : {result.reason}")
    return 0 if result.valid else 1


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if args:
        path = Path(args[0])
        if not path.exists():
            print(f"no such database: {path}", file=sys.stderr)
            return 2
        database_url = f"sqlite+aiosqlite:///{path.as_posix()}"
    else:
        database_url = get_settings().database_url
    return asyncio.run(_verify(database_url))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
