"""A batch that processes nothing must say so, not report completion.

Found by cloning the repository fresh and running the commands in the order a
curious reader might, rather than the order the README lists them. ``python
tasks.py batch`` on a clean clone printed::

    ======================================================================
    BATCH COMPLETE -- 0 cases
    ======================================================================
      treated     0
      control     0
      settled     0
      simulated recovery   Rs 0

and exited **zero**. There was no corpus -- SQLAlchemy had created the schema and
nothing had seeded it -- so there was nothing to process, and the run reported
success at processing nothing.

The second half is worse than the first. That run left an empty ``revpilot.db``
behind, and ``demo`` checked whether the *file* existed before deciding to seed.
So the next ``python tasks.py demo`` printed "database present", ran a batch over
an empty corpus, and served a judge a dashboard of zeroes with no indication that
anything was wrong. The command whose entire job is "everything a judge needs, in
one command" was one stray invocation away from silently producing nothing.

Both halves are the same mistake in different places: **treating the presence of
a container as evidence of its contents.** See INC-046.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.agent.nodes import AgentDeps
from app.core.clock import FakeClock
from app.workers.batch import EmptyCorpusError, run_batch

ROOT = Path(__file__).resolve().parents[1]
MOMENT = FakeClock.at_ist(2026, 9, 1, 11, 30)


@pytest_asyncio.fixture
async def empty(engine: AsyncEngine) -> AsyncEngine:
    """The schema, and nothing in it. Exactly what `batch` before `seed` leaves."""
    return engine


@pytest.mark.asyncio
class TestTheBatchRefusesAnEmptyCorpus:
    async def test_it_raises_rather_than_reporting_completion(self, empty: AsyncEngine) -> None:
        factory = async_sessionmaker(empty, expire_on_commit=False, autoflush=False)
        with pytest.raises(EmptyCorpusError):
            await run_batch(
                factory,
                clock=MOMENT,
                deps=AgentDeps(clock=MOMENT, adapter=None),
                limit=None,
            )

    async def test_the_error_names_the_command_that_fixes_it(self, empty: AsyncEngine) -> None:
        """A traceback tells a reader the project is broken. A sentence naming
        one command tells them they are one step from working."""
        factory = async_sessionmaker(empty, expire_on_commit=False, autoflush=False)
        with pytest.raises(EmptyCorpusError) as caught:
            await run_batch(
                factory, clock=MOMENT, deps=AgentDeps(clock=MOMENT, adapter=None), limit=None
            )
        message = str(caught.value)
        assert "tasks.py seed" in message
        assert "tasks.py demo" in message

    async def test_a_seeded_corpus_still_runs(self, seeded_engine: AsyncEngine) -> None:
        """The guard that makes the two tests above mean something.

        A check that refused *every* corpus would satisfy them both. This one
        proves the refusal is specific to emptiness.
        """
        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)
        result = await run_batch(
            factory, clock=MOMENT, deps=AgentDeps(clock=MOMENT, adapter=None), limit=8
        )
        assert result.cases_created > 0, "a seeded corpus must produce cases"


class TestDemoChecksTheCorpusNotTheFile:
    """`tasks.py demo` decided whether to seed by asking whether the database
    file existed. An empty file is a database that exists."""

    @staticmethod
    def _demo_source() -> str:
        text = (ROOT / "tasks.py").read_text(encoding="utf-8")
        start = text.index("def demo()")
        # Up to the next top-level decorator.
        end = text.index("@task(", start)
        return text[start:end]

    def test_the_source_was_found(self) -> None:
        assert len(self._demo_source()) > 500, "the demo task body could not be located"

    def test_it_counts_payment_attempts_before_deciding_to_seed(self) -> None:
        source = self._demo_source()
        assert "payment_attempts" in source, (
            "tasks.py demo no longer counts the corpus before deciding whether "
            "to seed. If it only checks that revpilot.db exists, an empty "
            "database left by a bare `batch` run is accepted as seeded and the "
            "dashboard serves zeroes (INC-046)."
        )

    def test_it_does_not_decide_on_file_existence_alone(self) -> None:
        """The specific line that was wrong: `if not runtime_db.exists()`.

        Its absence is the fix. Asserted on the source because the alternative
        is running the whole `demo` command inside a test, which starts two
        servers.
        """
        source = self._demo_source()
        assert not re.search(r"if not runtime_db\.exists\(\):\s*\n\s*seed_db", source), (
            "demo is back to branching on file existence alone"
        )

    def test_the_committed_seed_database_actually_holds_a_corpus(self) -> None:
        """The file `demo` copies. If this were empty, every check above would
        pass and a fresh clone would still show zeroes."""
        seed_db = ROOT / "data" / "revpilot.seed.db"
        assert seed_db.is_file(), "the committed demo database is missing"
        conn = sqlite3.connect(f"file:{seed_db}?mode=ro", uri=True)
        try:
            attempts = conn.execute("select count(*) from payment_attempts").fetchone()[0]
        finally:
            conn.close()
        assert attempts >= 400, (
            f"the committed seed database holds {attempts} payment attempts; the "
            "README promises a 420-transaction corpus"
        )
