"""The LLM accounting ledger: INC-026 and INC-027.

Two bugs, found by looking at a screenshot rather than by running a test.

**INC-026** — ``llm_calls`` had a reader and no writer. ``cost_report`` summed
it faithfully; nothing in the entire codebase, tests included, ever inserted a
row. So "Where the answers came from" rendered three empty bars and
"0 inferences · 0% served from cache" on every clone, forever. The existing
cost test passed *because* the feature was missing: it asserted on an empty
table and got the zeros it expected.

**INC-027** — the merged diagnosis was labelled ``DiagnosisSource.LLM``
whenever the adapter returned a valid structured output, regardless of which
layer produced it. With a cache miss and no live adapter the
``DeterministicAdapter`` answers — and 41 of 199 batch cases were stored as
model reasoning and traced as provenance "model" for answers no model produced.
``adapter.py``'s own docstring states the rule that broke: *"a deterministic
fallback must never be displayed as model reasoning"*.

The shared root cause is worth naming: **a structured output is not evidence
that a model produced it.** Every layer in the stack returns the same
``StructuredResult`` shape, on purpose, so the shape cannot be the signal. Only
``source`` can, and the code was ignoring it.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.nodes import AgentDeps, diagnose_node, strategise_node
from app.agent.state import LLMCallRecord, RecoveryState
from app.core.clock import FakeClock
from app.db.enums import (
    DiagnosisSource,
    FailureCategory,
    LLMSource,
    LLMTask,
    Playbook,
)
from app.db.models import LLMCall
from app.llm.adapter import StructuredResult
from app.llm.deterministic import DeterministicAdapter
from app.llm.routing import diagnose as routed_diagnose
from app.llm.schemas import DiagnosisOutput
from app.services.metrics import cost_report

# Fixed, injected. FakeClock takes no default (INC-023): a test that read
# the wall clock would drift with the time of day.
MOMENT = datetime(2026, 3, 1, 11, 0, tzinfo=UTC)

# A context the rule table declares itself unsure about, so routing actually
# reaches the adapter. This matters more than it looks: with a context the rule
# table CAN settle -- almost any real error_reason -- routing short-circuits and
# never calls the adapter at all, and every assertion below about layer
# labelling would pass vacuously. Razorpay omitting the error fields is the real
# condition that triggers a model consultation, and it is the whole reason the
# model is in the system.
UNSURE: dict[str, object] = {
    "error_source": None,
    "error_step": None,
    "error_reason": None,
    "method": "card",
    "playbook": "PAYMENT_FAILURE",
}


def test_the_unsure_context_really_is_unsure() -> None:
    """Guards every test in this file.

    If the rule table ever settles this context, routing stops calling the
    adapter and the layer-labelling assertions below become unfalsifiable --
    green, and proving nothing. That is INC-006 exactly, so it gets its own
    check rather than a comment.
    """
    from app.agent.classifier import classify

    diagnosis = classify(error_source=None, error_step=None, error_reason=None, method="card")
    assert diagnosis.needs_llm_review, (
        "the rule table now settles this context; routing will short-circuit "
        "and the layer-labelling tests below will pass without testing anything"
    )


class _SourceStub:
    """An adapter that answers with a chosen ``source``.

    The point of the fixture: the *output* is identical in every case. Only
    ``source`` differs. A test that varied the output too would not be able to
    tell whether the code under test was reading the source or guessing from
    the payload.
    """

    name = "stub"

    def __init__(self, source: LLMSource) -> None:
        self._source = source

    async def complete_structured(
        self,
        *,
        task: LLMTask,
        context: dict[str, Any],
        timeout_s: float | None = None,
        force_live: bool = False,
    ) -> StructuredResult:
        return StructuredResult(
            task=task,
            output=DiagnosisOutput(
                category=FailureCategory.INSUFFICIENT_FUNDS,
                is_recoverable=True,
                confidence=0.8,
                reasoning="stub",
            ),
            source=self._source,
            model="stub-model",
            provider="stub",
            prompt_version="v1",
            cache_key="deadbeef",
            input_tokens=120,
            output_tokens=40,
            latency_ms=7,
            fell_back=self._source is LLMSource.DETERMINISTIC,
        )


def _state() -> RecoveryState:
    """A case the rule table cannot settle, so routing reaches the adapter."""
    return RecoveryState(
        case_id="RC-LEDGER-1",
        merchant_id="mch_glowkart",
        customer_id="cus_0001",
        playbook=Playbook.PAYMENT_FAILURE,
        amount_paise=50_000,
        error_source=None,
        error_step=None,
        error_reason=None,
        method="card",
        consent_transactional=True,
        window_expires_at=MOMENT + timedelta(hours=24),
    )


def _deps(adapter: object | None) -> AgentDeps:
    return AgentDeps(clock=FakeClock(MOMENT), adapter=adapter)  # type: ignore[arg-type]


# ===========================================================================
class TestLedgerIsWritten:
    """INC-026: the record must exist at all."""

    async def test_diagnose_records_even_with_no_adapter(self) -> None:
        """The no-model path is the one worth recording most.

        A DETERMINISTIC row is not a gap in the data — it is the measurement
        behind "the rule table handled this and no token was spent", which is
        the claim the cost panel exists to make.
        """
        after = await diagnose_node(_state(), _deps(None))
        assert len(after.llm_ledger) == 1
        entry = after.llm_ledger[0]
        assert entry.task is LLMTask.DIAGNOSE
        assert entry.source is LLMSource.DETERMINISTIC
        assert entry.case_id == "RC-LEDGER-1"

    async def test_strategise_appends_rather_than_replaces(self) -> None:
        """Two decisions, two rows. An append that overwrote would still be
        non-empty, and a test asserting only non-emptiness would pass."""
        state = await diagnose_node(_state(), _deps(None))
        after = await strategise_node(state, _deps(None))
        assert [e.task for e in after.llm_ledger] == [LLMTask.DIAGNOSE, LLMTask.STRATEGISE]

    @pytest.mark.parametrize("source", list(LLMSource))
    async def test_every_source_is_recorded_verbatim(self, source: LLMSource) -> None:
        after = await diagnose_node(_state(), _deps(_SourceStub(source)))
        assert after.llm_ledger[-1].source is source

    async def test_tokens_are_carried_not_re_derived(self) -> None:
        """The adapter's own count reaches the row.

        Re-deriving token counts from the prompt would give the cost panel a
        number that drifts from what the provider actually billed for.
        """
        after = await diagnose_node(_state(), _deps(_SourceStub(LLMSource.LIVE)))
        entry = after.llm_ledger[-1]
        assert (entry.input_tokens, entry.output_tokens) == (120, 40)
        assert entry.cache_key == "deadbeef"
        assert entry.model == "stub-model"


# ===========================================================================
class TestSourceLabelling:
    """INC-027: the label must match the layer that answered."""

    @pytest.mark.parametrize(
        ("source", "expected"),
        [
            (LLMSource.LIVE, DiagnosisSource.LLM),
            (LLMSource.CACHED, DiagnosisSource.LLM),
            (LLMSource.DETERMINISTIC, DiagnosisSource.DETERMINISTIC_FALLBACK),
        ],
    )
    async def test_diagnosis_source_follows_the_layer(
        self, source: LLMSource, expected: DiagnosisSource
    ) -> None:
        routed = await routed_diagnose(UNSURE, adapter=_SourceStub(source))  # type: ignore[arg-type]
        assert routed.diagnosis.source is expected

    async def test_cached_counts_as_the_model(self) -> None:
        """A cached response is the model's own words, replayed.

        Calling it deterministic would be the opposite error to INC-027 and
        would understate what the model contributed.
        """
        routed = await routed_diagnose(UNSURE, adapter=_SourceStub(LLMSource.CACHED))  # type: ignore[arg-type]
        assert routed.consulted_model

    async def test_deterministic_floor_is_not_a_consultation(self) -> None:
        routed = await routed_diagnose(
            UNSURE,
            adapter=_SourceStub(LLMSource.DETERMINISTIC),  # type: ignore[arg-type]
        )
        assert not routed.consulted_model
        assert routed.model_category is None
        assert not routed.model_disagreed

    async def test_real_deterministic_adapter_is_never_labelled_llm(self) -> None:
        """The production floor, not a stub.

        The stub above could agree with a wrong implementation of itself. This
        drives the actual ``DeterministicAdapter`` that ships, which is what ran
        for the 41 mislabelled cases.
        """
        routed = await routed_diagnose(UNSURE, adapter=DeterministicAdapter())
        assert routed.diagnosis.source is DiagnosisSource.DETERMINISTIC_FALLBACK
        assert not routed.consulted_model

    async def test_trace_provenance_does_not_claim_a_model(self) -> None:
        """The string a merchant reads, not just the enum behind it."""
        after = await diagnose_node(_state(), _deps(_SourceStub(LLMSource.DETERMINISTIC)))
        provenance = after.trace[-1].provenance
        assert "rule table" in provenance
        assert not provenance.startswith("model")


# ===========================================================================
class TestCostReportReadsTheLedger:
    """The join INC-026 lived in: a writer and a reader, never tested together."""

    async def test_empty_ledger_reports_zero(self, session: AsyncSession) -> None:
        report = await cost_report(session)
        assert report.llm_calls == 0
        assert report.cache_hit_rate == 0.0

    async def test_rows_reach_the_report(self, session: AsyncSession) -> None:
        now = MOMENT
        for i, source in enumerate(
            [LLMSource.LIVE, LLMSource.CACHED, LLMSource.CACHED, LLMSource.DETERMINISTIC]
        ):
            session.add(
                LLMCall(
                    id=f"llmcall_{i}",
                    case_id=None,
                    task=LLMTask.DIAGNOSE,
                    source=source,
                    input_tokens=1000 if source is LLMSource.LIVE else 0,
                    output_tokens=500 if source is LLMSource.LIVE else 0,
                    created_at=now,
                )
            )
        await session.commit()

        report = await cost_report(session)
        assert report.llm_calls == 4
        assert report.by_source == {"LIVE": 1, "CACHED": 2, "DETERMINISTIC": 1}
        assert report.cache_hit_rate == pytest.approx(0.5)
        # A projection over real token counts must be non-zero, or the
        # "would this work in production" number is decorative.
        assert report.projected_spend.paise > 0

    async def test_deterministic_rows_do_not_inflate_the_projection(
        self, session: AsyncSession
    ) -> None:
        """Zero tokens must stay zero rupees.

        Charging for the rule table would make the cost panel argue against the
        routing decision it exists to justify.
        """
        for i in range(20):
            session.add(
                LLMCall(
                    id=f"det_{i}",
                    case_id=None,
                    task=LLMTask.STRATEGISE,
                    source=LLMSource.DETERMINISTIC,
                    created_at=MOMENT,
                )
            )
        await session.commit()
        report = await cost_report(session)
        assert report.llm_calls == 20
        assert report.projected_spend.paise == 0


# ===========================================================================
class TestBatchPersistsTheLedger:
    """The other half of the INC-026 join: state → rows."""

    async def test_batch_writes_a_row_per_record(self, seeded_engine: object) -> None:
        """Driven through the real batch, not a hand-built session.

        INC-026 was invisible to every unit test because no unit test crossed
        the graph/persistence boundary. Asserting on the database after a real
        run is the only shape that could have caught it.
        """
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.workers.batch import run_batch

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)  # type: ignore[arg-type]
        clock = FakeClock(MOMENT)
        await run_batch(factory, clock=clock, deps=_deps(None), limit=8)

        async with factory() as session:
            rows = int(await session.scalar(select(func.count(LLMCall.id))) or 0)
            cases = int(
                await session.scalar(select(func.count(func.distinct(LLMCall.case_id)))) or 0
            )
        assert rows > 0, "the batch ran and the ledger is still empty -- INC-026 has returned"
        assert cases > 0
        # Two decisions per case that reaches strategise, so the ledger must
        # out-number the cases it covers.
        assert rows >= cases

    async def test_rerunning_the_batch_does_not_double_the_ledger(
        self, seeded_engine: object
    ) -> None:
        """A judge who runs it twice sees the same numbers, not doubled ones."""
        from sqlalchemy.ext.asyncio import async_sessionmaker

        from app.workers.batch import run_batch

        factory = async_sessionmaker(seeded_engine, expire_on_commit=False, autoflush=False)  # type: ignore[arg-type]
        await run_batch(factory, clock=FakeClock(MOMENT), deps=_deps(None), limit=8)
        async with factory() as session:
            first = int(await session.scalar(select(func.count(LLMCall.id))) or 0)
        # Without this the test passes at 0 == 0 -- green with no writer at all,
        # which is precisely how INC-026 survived. Deleting the writer made this
        # test pass during sabotage until the guard was added.
        assert first > 0, "nothing was written, so 'not doubled' proves nothing"

        await run_batch(factory, clock=FakeClock(MOMENT), deps=_deps(None), limit=8)
        async with factory() as session:
            second = int(await session.scalar(select(func.count(LLMCall.id))) or 0)

        assert second == first


# ===========================================================================
class TestRecordShape:
    """Small guards on the record itself."""

    def test_a_record_defaults_to_a_zero_cost(self) -> None:
        """The projection is applied once, in the metrics service, over the
        token sum. A second copy of the rate table here is how the per-row cost
        and the dashboard total drift apart."""
        record = LLMCallRecord(task=LLMTask.DIAGNOSE, source=LLMSource.DETERMINISTIC)
        assert record.projected_cost_micro_inr == 0

    def test_records_are_immutable(self) -> None:
        record = LLMCallRecord(task=LLMTask.DIAGNOSE, source=LLMSource.LIVE)
        with pytest.raises(FrozenInstanceError):
            record.source = LLMSource.CACHED  # type: ignore[misc]
