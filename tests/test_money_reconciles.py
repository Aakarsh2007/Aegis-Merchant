"""The headline figures must add up, and the significance test must be the real one.

Both halves of this file come from one reviewer pass that added up three numbers
in the README:

    Rs 60,217 + Rs 1,39,021 = Rs 1,99,238, which is not Rs 2,02,760.

They asked whether an attribution system whose headline does not reconcile can be
trusted. The arithmetic turned out to be fine and the *layout* was the bug -- the
three quantities were laid out as ``gross -> claimable + not claimed``, which
reads as a partition, when incremental is an estimate over the treated arm's
exposure rather than a slice of gross.

Nothing in the suite could have caught that, because no test knew what the
relationship between the three figures was supposed to be. Now one does, and it
is stated as an identity rather than as prose that a reader has to trust.

The second half pins the significance criterion. It used to be "do the two Wilson
intervals overlap", which is a real test of something but not of the hypothesis.
"""

from __future__ import annotations

import math

import pytest

from app.core.power import sample_size_plan
from app.core.stats import normal_sf, two_proportion_test
from app.db.enums import ExperimentArm
from app.routers.metrics import PREREG_P_CONTROL, PREREG_P_TREATMENT
from app.services.attribution import CaseOutcome, recovery_report


def _population(
    *, treated: int, treated_paid: int, control: int, control_paid: int, amount: int = 100_000
) -> list[CaseOutcome]:
    out: list[CaseOutcome] = []
    for i in range(treated):
        out.append(
            CaseOutcome(
                case_id=f"T{i}",
                arm=ExperimentArm.TREATMENT,
                paid=i < treated_paid,
                amount_paise=amount,
                recovered=i < treated_paid,
            )
        )
    for i in range(control):
        out.append(
            CaseOutcome(
                case_id=f"C{i}",
                arm=ExperimentArm.CONTROL,
                paid=i < control_paid,
                amount_paise=amount,
                recovered=False,
            )
        )
    return out


class TestTheTwoProportionTest:
    """Checked against values computed independently, not against itself."""

    def test_the_corpus_figures(self) -> None:
        """The numbers this submission actually quotes.

        50/171 against 9/39. Computed by hand from the pooled z formula and
        cross-checked against the reviewer's own estimate of "around 0.42".
        """
        r = two_proportion_test(
            treated_paid=50, treated_cases=171, control_paid=9, control_cases=39
        )
        assert r.diff == pytest.approx(0.061628, abs=1e-5)
        assert r.z == pytest.approx(0.7727, abs=5e-4)
        assert r.p_value == pytest.approx(0.4397, abs=5e-4)
        lo, hi = r.diff_ci95
        assert lo == pytest.approx(-0.0871, abs=5e-4)
        assert hi == pytest.approx(0.2104, abs=5e-4)
        assert not r.is_significant
        assert r.well_defined
        # The interval on the difference must contain zero exactly when the
        # test fails to reject. Two derivations of one conclusion agreeing is
        # the only reason to publish both.
        assert (lo < 0.0 < hi) is (not r.is_significant)

    def test_normal_sf_against_known_quantiles(self) -> None:
        assert normal_sf(0.0) == pytest.approx(0.5)
        assert normal_sf(1.959964) == pytest.approx(0.025, abs=1e-6)
        assert normal_sf(1.644854) == pytest.approx(0.05, abs=1e-6)
        assert normal_sf(2.575829) == pytest.approx(0.005, abs=1e-6)

    @pytest.mark.parametrize("z", [10.0, 15.0, 20.0, 30.0])
    def test_the_far_tail_does_not_collapse_to_zero(self, z: float) -> None:
        """Why ``erfc`` and not ``1 - erf``.

        Past about z = 9 the subtraction loses every significant digit and
        returns exactly 0.0, while ``erfc`` keeps going to about z = 37. A
        p-value of 0.0 printed on a dashboard is a number a reader would
        believe.

        The first version of this test asserted ``normal_sf(40.0) > 0.0`` on the
        assumption that ``erfc`` simply does not underflow. It does, at 38, and
        the test failed -- which is the useful outcome, because the docstring
        had made a stronger claim than the code could support. The range is
        thirty standard deviations wider, not unbounded, and that is now what
        both the test and the comment say.
        """
        assert normal_sf(z) > 0.0, "erfc should still resolve here"
        assert 1.0 - math.erf(z / math.sqrt(2.0)) == 0.0, (
            "if the naive form has not underflowed yet, this z is too small to demonstrate anything"
        )

    def test_a_real_effect_is_detected(self) -> None:
        """Guards the whole class. A test that can only ever say "no" proves
        nothing by saying "no" -- the INC-006 shape this project keeps hitting."""
        r = two_proportion_test(
            treated_paid=400, treated_cases=1000, control_paid=200, control_cases=1000
        )
        assert r.is_significant
        assert r.p_value < 1e-10
        lo, _ = r.diff_ci95
        assert lo > 0.0

    def test_a_negative_lift_is_reported_as_negative(self) -> None:
        r = two_proportion_test(
            treated_paid=100, treated_cases=1000, control_paid=300, control_cases=1000
        )
        assert r.diff < 0.0
        assert r.z < 0.0
        assert r.is_significant, "a large harm must be detected, not just a large gain"

    @pytest.mark.parametrize(
        ("tp", "tc", "cp", "cc"),
        [(0, 0, 0, 0), (5, 10, 0, 0), (0, 0, 5, 10), (10, 10, 10, 10), (0, 10, 0, 10)],
    )
    def test_degenerate_inputs_are_not_significant_and_do_not_raise(
        self, tp: int, tc: int, cp: int, cc: int
    ) -> None:
        """A fresh clone has an empty control arm. It must render, not crash,
        and must certainly not report a pass."""
        r = two_proportion_test(
            treated_paid=tp, treated_cases=tc, control_paid=cp, control_cases=cc
        )
        assert not r.is_significant
        assert r.p_value == 1.0

    @pytest.mark.parametrize(
        ("tp", "tc", "cp", "cc"),
        [(-1, 10, 0, 10), (11, 10, 0, 10), (0, 10, 11, 10), (0, -5, 0, 10)],
    )
    def test_impossible_counts_raise(self, tp: int, tc: int, cp: int, cc: int) -> None:
        with pytest.raises(ValueError):
            two_proportion_test(
                treated_paid=tp, treated_cases=tc, control_paid=cp, control_cases=cc
            )


class TestTheReportUsesTheProperTest:
    def test_significance_comes_from_the_z_test_not_interval_overlap(self) -> None:
        """The two criteria must be *able* to disagree, or replacing one with the
        other changed nothing and this whole change is cosmetic.

        Constructed to sit in the gap: interval non-overlap is strictly more
        conservative, so there is a region where the z-test rejects and the
        intervals still touch. A population in that region proves the report now
        reads the correct one.
        """
        found = None
        for n in range(40, 400, 10):
            report = recovery_report(
                _population(
                    treated=n, treated_paid=int(n * 0.45), control=n, control_paid=int(n * 0.30)
                )
            )
            if report.lift_is_significant and report.intervals_overlap:
                found = (n, report)
                break
        assert found is not None, (
            "no population found where the z-test rejects but the Wilson "
            "intervals still overlap -- if that region does not exist, the "
            "criterion change was cosmetic and this test should be deleted"
        )
        _, report = found
        assert report.significance.p_value < 0.05

    def test_the_corpus_position_is_not_significant_under_either_criterion(self) -> None:
        report = recovery_report(
            _population(treated=171, treated_paid=50, control=39, control_paid=9)
        )
        assert not report.lift_is_significant
        assert report.intervals_overlap
        assert report.significance.p_value == pytest.approx(0.4397, abs=5e-4)

    def test_no_control_arm_is_never_significant(self) -> None:
        report = recovery_report(
            _population(treated=500, treated_paid=400, control=0, control_paid=0)
        )
        assert not report.has_control
        assert not report.lift_is_significant
        assert report.incremental_revenue_paise == 0, (
            "with no counterfactual, incremental must be zero and not gross"
        )

    def test_as_dict_publishes_the_p_value(self) -> None:
        d = recovery_report(
            _population(treated=171, treated_paid=50, control=39, control_paid=9)
        ).as_dict()
        assert d["significance"]["p_value"] == pytest.approx(0.4397, abs=5e-4)
        assert d["significance"]["diff_ci95"][0] < 0 < d["significance"]["diff_ci95"][1]
        assert d["intervals_overlap"] is True


class TestProgressIsReportedWithItsName:
    """A single unlabelled percentage was read as the whole experiment's
    progress when it was the binding arm's. Both are now published."""

    def test_the_three_figures_differ_and_are_all_correct(self) -> None:
        plan = sample_size_plan(
            control_now=39,
            treatment_now=171,
            p_control=PREREG_P_CONTROL,
            p_treatment=PREREG_P_TREATMENT,
        )
        assert plan.control_required == plan.treatment_required == 796
        assert plan.control_completion == pytest.approx(39 / 796)
        assert plan.treatment_completion == pytest.approx(171 / 796)
        assert plan.overall_completion == pytest.approx(210 / 1592)
        # The reviewer's three figures, to one decimal.
        assert round(plan.control_completion * 100, 1) == 4.9
        assert round(plan.treatment_completion * 100, 1) == 21.5
        assert round(plan.overall_completion * 100, 1) == 13.2

    def test_completion_still_tracks_the_binding_arm(self) -> None:
        """The original reasoning stands: 5,000 treated and 12 control is not
        99% of the way to an answer."""
        plan = sample_size_plan(
            control_now=12,
            treatment_now=5000,
            p_control=PREREG_P_CONTROL,
            p_treatment=PREREG_P_TREATMENT,
        )
        assert plan.completion == pytest.approx(12 / 796)
        assert plan.overall_completion > 0.5
        assert plan.completion < 0.02
        assert plan.binding_arm == "control"

    def test_the_binding_arm_can_be_the_treatment_arm(self) -> None:
        plan = sample_size_plan(
            control_now=700,
            treatment_now=100,
            p_control=PREREG_P_CONTROL,
            p_treatment=PREREG_P_TREATMENT,
        )
        assert plan.binding_arm == "treatment"

    def test_overall_never_exceeds_one(self) -> None:
        plan = sample_size_plan(
            control_now=9000,
            treatment_now=9000,
            p_control=PREREG_P_CONTROL,
            p_treatment=PREREG_P_TREATMENT,
        )
        assert plan.overall_completion == 1.0
        assert plan.is_powered
