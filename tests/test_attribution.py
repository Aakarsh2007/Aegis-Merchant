"""Attribution and experiment tests.

The headline figure lives or dies here. Most of these assert that something is
**not** counted, because every one of the six conditions can only reduce what
we report, and a rule that never refuses is not a rule.

Note what is absent, deliberately (workflow.md §16.2): **no test asserts a
target rupee figure.** Asserting `recovered == 124300` would be a test that the
simulation was rigged. These assert invariants — that gross equals the sum of
verified webhooks, that control money is never counted, that lift can be
negative.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.core.stats import wilson_bounds
from app.db.enums import CaseStatus, ExperimentArm
from app.services.attribution import (
    SETTLING_EVENTS,
    CaseOutcome,
    attribute,
    recovery_report,
)
from app.services.experiments import assign_arm

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
GRACE = timedelta(hours=24)
REF = "rvp_rc-0142_1"


def attempt(**overrides: object):  # type: ignore[no-untyped-def]
    """A webhook that WOULD count, so each test breaks exactly one condition."""
    base: dict[str, object] = {
        "event_type": "payment_link.paid",
        "signature_valid": True,
        "event_id": "evt_signed_001",
        "reference_id": REF,
        "webhook_amount_paise": 429_900,
        "issued_reference_id": REF,
        "case_status": CaseStatus.MONITORING,
        "case_amount_paise": 429_900,
        "already_counted": False,
        "now": NOW,
        "window_expires_at": NOW + timedelta(hours=12),
        "grace": GRACE,
    }
    base.update(overrides)
    return attribute(**base)  # type: ignore[arg-type]


# ===========================================================================
class TestTheSixConditions:
    def test_a_clean_settlement_counts(self) -> None:
        result = attempt()
        assert result.counted
        assert result.amount_paise == 429_900
        assert result.verified_by == "evt_signed_001"

    def test_an_unsigned_event_never_counts(self) -> None:
        """We cannot count a payment we merely believe in."""
        assert not attempt(signature_valid=False)

    @pytest.mark.parametrize(
        "event_type", ["payment.captured", "order.paid", "payment.failed", "refund.created"]
    )
    def test_a_non_settling_event_never_counts(self, event_type: str) -> None:
        """`payment.captured` is excluded on purpose: it fires for organic
        checkout completions too, so using it would count payments we had
        nothing to do with."""
        assert not attempt(event_type=event_type)

    def test_a_reference_we_did_not_issue_never_counts(self) -> None:
        """The line between attribution and coincidence, and where most
        dashboards quietly cheat."""
        result = attempt(reference_id="rvp_someone-else_1")
        assert not result.counted
        assert result.resolves_organically

    def test_a_missing_reference_never_counts(self) -> None:
        assert not attempt(reference_id=None)
        assert not attempt(issued_reference_id=None)

    @pytest.mark.parametrize(
        "status",
        [
            CaseStatus.DETECTED,
            CaseStatus.TRIAGED,
            CaseStatus.AWAITING_APPROVAL,
            CaseStatus.SUPPRESSED,
            CaseStatus.EXPIRED,
            CaseStatus.RESOLVED_ORGANIC,
        ],
    )
    def test_a_case_we_did_not_act_on_never_counts(self, status: CaseStatus) -> None:
        """A control-arm case that pays is the counterfactual, not a recovery.
        Counting it would destroy the measurement it exists to provide."""
        result = attempt(case_status=status)
        assert not result.counted
        assert result.resolves_organically

    def test_a_payment_after_the_window_never_counts(self) -> None:
        result = attempt(window_expires_at=NOW - timedelta(days=3))
        assert not result.counted
        assert "window" in result.reason

    def test_the_grace_period_is_honoured(self) -> None:
        """A payment just inside the grace still counts -- the window is about
        attribution, and a customer who paid 20 hours after expiry did so
        because of our link."""
        assert attempt(window_expires_at=NOW - timedelta(hours=20))
        assert not attempt(window_expires_at=NOW - timedelta(hours=25))

    def test_double_counting_is_refused(self) -> None:
        result = attempt(already_counted=True)
        assert not result.counted
        assert not result.resolves_organically  # already handled, not organic


class TestAmountHandling:
    def test_the_case_amount_is_authoritative(self) -> None:
        """A webhook claiming a larger figure must not inflate the metric."""
        result = attempt(webhook_amount_paise=9_999_900, case_amount_paise=429_900)
        assert result.counted
        assert result.amount_paise == 429_900
        assert "webhook said" in result.reason

    def test_a_smaller_webhook_amount_wins(self) -> None:
        """Partial settlement: count what actually arrived, not what we hoped."""
        result = attempt(webhook_amount_paise=100_000, case_amount_paise=429_900)
        assert result.amount_paise == 100_000

    def test_a_missing_webhook_amount_falls_back_to_the_case(self) -> None:
        assert attempt(webhook_amount_paise=None).amount_paise == 429_900


class TestReferenceMatching:
    def test_matching_is_case_insensitive(self) -> None:
        """We emit lowercase (INC-012), but a provider echoing different casing
        must not silently break attribution -- Razorpay's own error messages
        do exactly that. Being lenient costs nothing on a high-entropy string
        we generated; being strict costs an uncounted recovery."""
        assert attempt(reference_id=REF.upper())

    def test_whitespace_is_tolerated(self) -> None:
        assert attempt(reference_id=f"  {REF}  ")

    def test_a_near_miss_is_still_a_miss(self) -> None:
        assert not attempt(reference_id=REF + "x")
        assert not attempt(reference_id=REF.replace("_1", "_2"))


# ===========================================================================
class TestArmAssignment:
    def test_the_same_case_always_lands_in_the_same_arm(self) -> None:
        """Stable across restart, redelivery and replay -- otherwise a case
        could be control on Monday and treated on Tuesday."""
        first = assign_arm("RC-0142", experiment_key="k", control_fraction=0.18)
        second = assign_arm("RC-0142", experiment_key="k", control_fraction=0.18)
        assert first.arm is second.arm
        assert first.assignment_hash == second.assignment_hash

    def test_a_different_experiment_key_reshuffles(self) -> None:
        a = assign_arm("RC-0142", experiment_key="k1", control_fraction=0.18)
        b = assign_arm("RC-0142", experiment_key="k2", control_fraction=0.18)
        assert a.assignment_hash != b.assignment_hash

    def test_the_split_is_close_to_the_target(self) -> None:
        arms = [
            assign_arm(f"RC-{i:05d}", experiment_key="k", control_fraction=0.18)
            for i in range(4000)
        ]
        control = sum(1 for a in arms if a.is_control) / len(arms)
        assert 0.16 < control < 0.20, f"split drifted to {control:.1%}"

    def test_zero_fraction_disables_the_holdout(self) -> None:
        arms = [
            assign_arm(f"RC-{i:04d}", experiment_key="k", control_fraction=0.0) for i in range(500)
        ]
        assert all(a.arm is ExperimentArm.TREATMENT for a in arms)

    def test_a_fraction_of_one_is_rejected(self) -> None:
        """It would mean never acting at all."""
        with pytest.raises(ValueError, match=r"\[0.0, 1.0\)"):
            assign_arm("RC-1", experiment_key="k", control_fraction=1.0)

    def test_assignment_ignores_everything_correlated_with_outcome(self) -> None:
        """Assigning by amount would put the easy recoveries in one arm and
        make the lift a measurement of the split rather than the intervention.

        The signature takes only the case id, so there is no channel through
        which amount or LTV could influence the arm.
        """
        import inspect

        source = inspect.signature(assign_arm)
        assert set(source.parameters) == {"case_id", "experiment_key", "control_fraction"}


# ===========================================================================
class TestRecoveryReport:
    @staticmethod
    def population(
        *, treated: int, treated_paid: int, control: int, control_paid: int, amount: int = 400_000
    ) -> list[CaseOutcome]:
        out = []
        for i in range(treated):
            paid = i < treated_paid
            out.append(
                CaseOutcome(
                    case_id=f"T{i}",
                    arm=ExperimentArm.TREATMENT,
                    paid=paid,
                    amount_paise=amount,
                    recovered=paid,
                )
            )
        for i in range(control):
            out.append(
                CaseOutcome(
                    case_id=f"C{i}",
                    arm=ExperimentArm.CONTROL,
                    paid=i < control_paid,
                    amount_paise=amount,
                    recovered=False,  # control never has an issued reference
                )
            )
        return out

    def test_gross_is_the_sum_of_verified_recoveries(self) -> None:
        """An invariant, not a target figure. Asserting a specific rupee value
        would be a test that the simulation was rigged (§16.2)."""
        report = recovery_report(
            self.population(treated=100, treated_paid=30, control=20, control_paid=4)
        )
        assert report.gross_recovered_paise == 30 * 400_000

    def test_control_money_is_never_counted_as_recovered(self) -> None:
        report = recovery_report(
            self.population(treated=10, treated_paid=0, control=10, control_paid=10)
        )
        assert report.gross_recovered_paise == 0
        assert report.control.paid == 10

    def test_incremental_is_less_than_gross_when_control_converts(self) -> None:
        """The whole point. If 20% of the control paid anyway, we did not cause
        all of the treatment's 30%."""
        report = recovery_report(
            self.population(treated=100, treated_paid=30, control=100, control_paid=20)
        )
        assert report.absolute_lift == pytest.approx(0.10)
        assert 0 < report.incremental_revenue_paise < report.gross_recovered_paise

    def test_a_negative_lift_is_reported_as_negative(self) -> None:
        """A system that clamped this at zero could not tell you it was not
        working."""
        report = recovery_report(
            self.population(treated=100, treated_paid=10, control=100, control_paid=25)
        )
        assert report.absolute_lift < 0
        assert report.incremental_revenue_paise < 0

    def test_with_no_control_arm_incremental_is_not_claimed(self) -> None:
        """Claiming gross as incremental is exactly the overstatement the
        control group exists to prevent."""
        report = recovery_report(
            self.population(treated=50, treated_paid=20, control=0, control_paid=0)
        )
        assert report.gross_recovered_paise > 0
        assert report.incremental_revenue_paise == 0
        assert not report.has_control
        assert any("cannot be computed" in n for n in report.notes)

    def test_costs_are_subtracted_from_the_net_figure(self) -> None:
        outcomes = self.population(treated=100, treated_paid=30, control=100, control_paid=20)
        outcomes = [
            CaseOutcome(**{**o.__dict__, "discount_paise": 5_000 if o.recovered else 0})
            for o in outcomes
        ]
        report = recovery_report(outcomes)
        assert report.discount_cost_paise == 30 * 5_000
        assert report.net_incremental_paise < report.incremental_revenue_paise

    def test_demo_cases_are_excluded(self) -> None:
        """Demo injections are demonstrations of mechanism, not data points --
        always treated and always chosen to succeed, so including them would
        bias the result upward (§14.4)."""
        outcomes = self.population(treated=10, treated_paid=5, control=10, control_paid=2)
        outcomes += [
            CaseOutcome(
                case_id=f"DEMO{i}",
                arm=ExperimentArm.TREATMENT,
                paid=True,
                amount_paise=999_999,
                recovered=True,
                is_demo=True,
            )
            for i in range(5)
        ]
        report = recovery_report(outcomes)
        assert report.treatment.cases == 10
        assert report.excluded_demo == 5
        assert report.gross_recovered_paise == 5 * 400_000
        assert any("demo" in n.lower() for n in report.notes)

    def test_an_empty_population_does_not_divide_by_zero(self) -> None:
        report = recovery_report([])
        assert report.treatment.conversion == 0.0
        assert report.control.conversion == 0.0
        assert report.incremental_revenue_paise == 0

    def test_a_small_control_arm_is_flagged_not_hidden(self) -> None:
        report = recovery_report(
            self.population(treated=100, treated_paid=40, control=8, control_paid=1)
        )
        assert any("directional" in n for n in report.notes)

    def test_significance_is_conservative_at_small_n(self) -> None:
        """With a hackathon-sized batch this should usually say "not
        significant", and saying so is the honest answer."""
        small = recovery_report(
            self.population(treated=10, treated_paid=4, control=10, control_paid=2)
        )
        assert not small.lift_is_significant

        large = recovery_report(
            self.population(treated=2000, treated_paid=800, control=2000, control_paid=200)
        )
        assert large.lift_is_significant

    def test_the_api_shape_shows_gross_and_net_together(self) -> None:
        """A tile showing gross alone is the number this module exists to
        qualify."""
        payload = recovery_report(
            self.population(treated=50, treated_paid=15, control=50, control_paid=10)
        ).as_dict()
        assert "gross_recovered_paise" in payload
        assert "net_incremental_paise" in payload
        assert "ci95" in payload["treatment"]
        assert payload["has_control_arm"] is True


class TestConfidenceIntervals:
    def test_intervals_widen_as_the_sample_shrinks(self) -> None:
        small = wilson_bounds(3, 10)
        large = wilson_bounds(300, 1000)
        assert (small[1] - small[0]) > (large[1] - large[0])

    def test_zero_of_ten_is_not_certainly_zero(self) -> None:
        lower, upper = wilson_bounds(0, 10)
        assert lower == 0.0
        assert upper > 0.2

    def test_no_trials_is_maximum_uncertainty(self) -> None:
        assert wilson_bounds(0, 0) == (0.0, 1.0)


class TestSettlingEvents:
    def test_captured_is_deliberately_excluded(self) -> None:
        assert "payment.captured" not in SETTLING_EVENTS
        assert "order.paid" not in SETTLING_EVENTS

    def test_the_settling_set_is_small_and_explicit(self) -> None:
        assert {"payment_link.paid", "invoice.paid", "subscription.charged"} == SETTLING_EVENTS
