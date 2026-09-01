"""The ablation table: does the architecture earn its complexity?

A reviewer asked the question an architecture diagram cannot answer — *"where
is the compelling advantage?"* — and the honest reply is a measurement of what
the architecture prevents, not a description of it.

These tests exist because a benchmark that flatters its author is worse than no
benchmark. So they assert the shape that makes the table *falsifiable*:

* the breach counts are **real** and must be non-zero for the unsafe arms —
  a table where every arm scores zero breaches proves nothing;
* RevPilot must score **exactly** zero, or the firewall claim is false;
* the arms must be comparable — same corpus, same seed, deterministic;
* and the honest limitations must stay in the output, because the recovery
  half of the table is a declared model and a reader who forgets that will
  over-quote it.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.workers.benchmark import (
    CONTROL_FRACTION,
    DISCOUNT_CEILING_PCT,
    NAIVE_DISCOUNT_PCT,
    BenchmarkReport,
    _in_quiet_hours,
    run_benchmark,
)


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


async def _run(engine: AsyncEngine) -> BenchmarkReport:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return await run_benchmark(factory)


def _arm(report: BenchmarkReport, name: str) -> object:
    match = next((a for a in report.arms if a.arm == name), None)
    assert match is not None, f"no arm named {name!r}"
    return match


# ===========================================================================
class TestTheTableIsNotVacuous:
    """A benchmark where nothing differs measures nothing."""

    async def test_the_corpus_is_not_empty(self, seeded_engine: AsyncEngine) -> None:
        report = await _run(seeded_engine)
        assert report.corpus_cases > 100, (
            f"only {report.corpus_cases} cases; every arm would be within noise "
            "of every other and the table would be decorative"
        )

    async def test_the_unsafe_arms_actually_breach(self, seeded_engine: AsyncEngine) -> None:
        """**The assertion that makes the safe arms mean something.**

        If `contact_everyone` scored zero breaches, RevPilot's zero would be
        unremarkable — it would just mean the corpus contains nothing to breach.
        """
        report = await _run(seeded_engine)
        naive = _arm(report, "contact_everyone")
        assert naive.total_breaches > 50, (  # type: ignore[attr-defined]
            "the naive arm barely breaches anything, so this corpus cannot "
            "distinguish a safe policy from an unsafe one"
        )

    async def test_each_breach_kind_is_exercised(self, seeded_engine: AsyncEngine) -> None:
        """All five bounds must be reachable, or some of them are untested
        claims dressed as measured zeroes."""
        report = await _run(seeded_engine)
        naive = _arm(report, "contact_everyone")
        for kind, count in naive.breaches.items():  # type: ignore[attr-defined]
            assert count > 0, (
                f"no arm ever breaches {kind}, so RevPilot's zero for it is "
                "not evidence of anything"
            )


# ===========================================================================
class TestTheFirewallClaimIsTrue:
    async def test_revpilot_breaches_nothing(self, seeded_engine: AsyncEngine) -> None:
        report = await _run(seeded_engine)
        shipped = _arm(report, "revpilot")
        assert shipped.total_breaches == 0, (  # type: ignore[attr-defined]
            f"RevPilot breached a hard bound {shipped.total_breaches} times. "  # type: ignore[attr-defined]
            "The firewall claim is false."
        )

    async def test_removing_the_firewall_causes_breaches(self, seeded_engine: AsyncEngine) -> None:
        """The ablation. Without this, "the firewall works" is an assertion
        about code that was never removed to see what happened."""
        report = await _run(seeded_engine)
        ablated = _arm(report, "no_firewall")
        assert ablated.total_breaches > 100, (  # type: ignore[attr-defined]
            "removing the firewall changed nothing, which means either the "
            "ablation is not really removing it or the firewall does nothing"
        )

    async def test_the_firewall_costs_nothing_in_recovery(self, seeded_engine: AsyncEngine) -> None:
        """The finding worth the whole exercise.

        Safety is usually presented as a trade-off against results. Here it is
        not: the firewall arm and the no-firewall arm recover the same amount,
        because the clamps affect *how* an action is taken and not *whether*.
        Asserted so that if a future change makes it a trade-off, we find out.
        """
        report = await _run(seeded_engine)
        shipped = _arm(report, "revpilot")
        ablated = _arm(report, "no_firewall")
        assert shipped.recovered_paise == ablated.recovered_paise, (  # type: ignore[attr-defined]
            "the firewall now costs recovery; the report's headline finding is "
            "stale and must be rewritten"
        )

    async def test_opt_out_survives_the_ablation(self, seeded_engine: AsyncEngine) -> None:
        """Opt-out is checked in the stopping rules, *before* the firewall, so
        removing the firewall must not start contacting opted-out customers.
        This pins that ordering."""
        report = await _run(seeded_engine)
        ablated = _arm(report, "no_firewall")
        assert ablated.breaches["contacted_opted_out"] == 0, (  # type: ignore[attr-defined]
            "removing the firewall began contacting opted-out customers, so "
            "opt-out is being enforced in the wrong layer"
        )


# ===========================================================================
class TestAttributionIsTheProduct:
    async def test_removing_the_holdout_recovers_more(self, seeded_engine: AsyncEngine) -> None:
        """Contacting the holdout too naturally recovers more. That is the
        temptation the design refuses."""
        report = await _run(seeded_engine)
        shipped = _arm(report, "revpilot")
        no_holdout = _arm(report, "no_holdout")
        assert no_holdout.recovered_paise > shipped.recovered_paise  # type: ignore[attr-defined]

    async def test_removing_the_holdout_destroys_attribution(
        self, seeded_engine: AsyncEngine
    ) -> None:
        """**The central claim of the project, as an assertion.**

        A bigger number with nothing behind it. `incremental_paise` must be
        None — not zero, not the gross figure — because there is no
        counterfactual to subtract.
        """
        report = await _run(seeded_engine)
        no_holdout = _arm(report, "no_holdout")
        assert no_holdout.incremental_paise is None  # type: ignore[attr-defined]
        assert not no_holdout.attribution_possible  # type: ignore[attr-defined]

    async def test_the_shipped_arm_can_attribute(self, seeded_engine: AsyncEngine) -> None:
        report = await _run(seeded_engine)
        shipped = _arm(report, "revpilot")
        assert shipped.attribution_possible  # type: ignore[attr-defined]
        assert shipped.incremental_paise is not None  # type: ignore[attr-defined]

    async def test_claimable_is_far_below_gross(self, seeded_engine: AsyncEngine) -> None:
        """The whole thesis in one comparison: what you may claim is a fraction
        of what you recovered."""
        report = await _run(seeded_engine)
        shipped = _arm(report, "revpilot")
        gross = shipped.recovered_paise  # type: ignore[attr-defined]
        claimable = shipped.incremental_paise  # type: ignore[attr-defined]
        assert claimable is not None
        assert claimable < gross * 0.6, (
            "claimable is close to gross, which would mean the holdout is "
            "barely converting -- check the response model"
        )

    async def test_no_intervention_reports_not_applicable(self, seeded_engine: AsyncEngine) -> None:
        """It holds cases back and contacts nobody, so it has a control arm and
        no treated arm. It must not claim attribution is available -- an earlier
        version printed "yes" next to a claimable of "--"."""
        report = await _run(seeded_engine)
        idle = _arm(report, "no_intervention")
        assert idle.has_holdout  # type: ignore[attr-defined]
        assert not idle.attribution_possible  # type: ignore[attr-defined]
        assert idle.recovered_paise == 0  # type: ignore[attr-defined]


# ===========================================================================
class TestTheArmsAreComparable:
    async def test_every_arm_sees_the_same_cases(self, seeded_engine: AsyncEngine) -> None:
        report = await _run(seeded_engine)
        counts = {a.cases for a in report.arms}
        assert len(counts) == 1, f"arms saw different case counts: {counts}"

    async def test_the_run_is_deterministic(self, seeded_engine: AsyncEngine) -> None:
        """Two runs must agree exactly. A table that moves between runs cannot
        support a claim about the difference between two of its rows."""
        first = await _run(seeded_engine)
        second = await _run(seeded_engine)
        assert [a.as_dict() for a in first.arms] == [a.as_dict() for a in second.arms]

    async def test_holdout_arms_hold_the_same_cases_back(self, seeded_engine: AsyncEngine) -> None:
        """Arm assignment is a hash of the case id, so every arm that has a
        holdout must hold back the *same* cases -- otherwise the arms differ by
        which cases they saw, not by policy."""
        report = await _run(seeded_engine)
        held = {a.held_as_control for a in report.arms if a.arm != "no_holdout"}
        assert len(held) == 1, f"holdout sizes differ across arms: {held}"


# ===========================================================================
class TestTheHonestyStaysInTheOutput:
    """The recovery half of this table is a declared model. A reader who forgets
    that will over-quote it, so the output has to keep saying so."""

    async def test_the_report_separates_measured_from_declared(
        self, seeded_engine: AsyncEngine
    ) -> None:
        rendered = (await _run(seeded_engine)).render()
        assert "MEASURED" in rendered
        assert "DECLARED" in rendered
        assert "no simulation in this table" in rendered

    async def test_the_limitations_are_printed(self, seeded_engine: AsyncEngine) -> None:
        rendered = (await _run(seeded_engine)).render()
        assert "WHAT THIS DOES NOT SHOW" in rendered
        # The absent LLM-only arm is the limitation most likely to be asked
        # about, so it must be volunteered rather than waited for.
        assert "no" in rendered and "LLM-only arm" in rendered
        assert "96.5%" in rendered and "90.6%" in rendered

    async def test_the_payload_carries_the_caveats_too(self, seeded_engine: AsyncEngine) -> None:
        """Not only the rendered text. A client reading the JSON must get the
        same warning."""
        body = (await _run(seeded_engine)).as_dict()
        assert "what_is_measured" in body
        assert "what_is_declared" in body
        declared = body["what_is_declared"].lower()
        # The substantive caveat, not a word I guessed might be in the sentence.
        # The first version of this test asserted on "declared" or "parameter",
        # neither of which the text used -- so it failed against perfectly
        # honest prose and told me nothing about whether the caveat was there.
        assert "not observations of customer behaviour" in declared
        assert "declared" in declared
        measured = body["what_is_measured"].lower()
        assert "no simulation" in measured

    async def test_no_arm_claims_statistical_significance(self, seeded_engine: AsyncEngine) -> None:
        """At these sample sizes nothing should reach significance, and the
        report must not imply otherwise."""
        report = await _run(seeded_engine)
        for arm in report.arms:
            assert not arm.significant, (
                f"{arm.arm} claims a significant lift at n={arm.treated_total}; "
                "check the interval arithmetic before believing it"
            )


# ===========================================================================
class TestQuietHours:
    """The one piece of date arithmetic in the harness."""

    @pytest.mark.parametrize(
        ("utc_hour", "expected"),
        [
            (17, True),  # 22:30 IST -- inside quiet hours
            (20, True),  # 01:30 IST
            (2, True),  # 07:30 IST
            (5, False),  # 10:30 IST -- allowed
            (12, False),  # 17:30 IST -- allowed
        ],
    )
    def test_ist_conversion(self, utc_hour: int, expected: bool) -> None:
        moment = datetime(2026, 9, 1, utc_hour, 0, tzinfo=UTC)
        assert _in_quiet_hours(moment) is expected

    def test_the_naive_discount_is_actually_over_the_ceiling(self) -> None:
        """Otherwise the discount-breach column would read zero for every arm
        and the row would be decoration."""
        assert NAIVE_DISCOUNT_PCT > DISCOUNT_CEILING_PCT

    def test_the_control_fraction_is_a_real_holdout(self) -> None:
        assert 0.05 < CONTROL_FRACTION < 0.5
