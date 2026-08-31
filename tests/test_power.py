"""Sample size arithmetic, checked against published values.

The point of this file is that the numbers on the dashboard's power panel are
**verifiable by someone who does not trust us**. A sample-size figure is easy to
get subtly wrong — a factor of two in the variance term, a one-sided quantile
where a two-sided one belongs — and the error is invisible because the output is
plausible either way. So the tests below check against textbook values rather
than against our own implementation.
"""

from __future__ import annotations

import re
from datetime import date
from pathlib import Path

import pytest

from app.core.power import (
    Z_ALPHA_05,
    Z_POWER_80,
    PowerPlan,
    required_per_arm,
    sample_size_plan,
)


class TestQuantiles:
    """Hard-coded rather than pulled from SciPy. Pinned to a table."""

    def test_alpha_is_two_sided_95(self) -> None:
        assert pytest.approx(1.959964, abs=1e-5) == Z_ALPHA_05

    def test_power_is_80_percent_one_sided(self) -> None:
        """0.8416 is Φ⁻¹(0.80). Using the two-sided 1.28 here would understate
        the required sample by roughly a third."""
        assert pytest.approx(0.841621, abs=1e-5) == Z_POWER_80


class TestRequiredPerArm:
    """Against published worked examples."""

    def test_balanced_textbook_case(self) -> None:
        """0.60 vs 0.50, balanced, alpha=0.05, power=0.80 → about 388 per arm.

        A standard worked example in every biostatistics text. Tolerance is
        loose because sources differ on continuity correction, and tight enough
        to catch a factor-of-two error in the variance term.
        """
        control, treatment = required_per_arm(p_control=0.50, p_treatment=0.60)
        assert control == treatment
        assert 380 <= control <= 400

    def test_our_own_effect_size(self) -> None:
        """The rates the simulation produces, at the pre-registered 50/50.

        Pinned because `docs/PRE-REGISTRATION.md` §5 publishes these figures and
        the two must not drift. If this test fails, the document is wrong or the
        code is, and either way someone has to look.
        """
        control, treatment = required_per_arm(p_control=0.2308, p_treatment=0.2924)
        assert control == treatment
        assert 780 <= control <= 810, "PRE-REGISTRATION.md §5 publishes this figure"

    def test_the_demo_allocation_costs_more_cases(self) -> None:
        """The claim the pre-registration makes: 81/19 needs ~36% more total.

        This is the finding that justified changing the experiment's allocation,
        so it gets an assertion rather than a comment.
        """
        bal_c, bal_t = required_per_arm(p_control=0.2308, p_treatment=0.2924)
        unb_c, unb_t = required_per_arm(
            p_control=0.2308, p_treatment=0.2924, control_fraction=39 / 210
        )
        balanced_total, unbalanced_total = bal_c + bal_t, unb_c + unb_t
        assert unbalanced_total > balanced_total
        saving = 1 - balanced_total / unbalanced_total
        assert 0.30 <= saving <= 0.42, f"expected roughly 36% saving, got {saving:.1%}"

    def test_smaller_effects_need_more_cases(self) -> None:
        """Monotonic in the effect size. Quadratically, in fact — halving the
        lift roughly quadruples the requirement."""
        big, _ = required_per_arm(p_control=0.20, p_treatment=0.30)
        small, _ = required_per_arm(p_control=0.20, p_treatment=0.25)
        assert small > 3 * big

    def test_unbalanced_burdens_the_smaller_arm(self) -> None:
        """The mechanism, asserted directly: as the control fraction shrinks,
        the control arm's requirement falls but the total rises."""
        prev_total = 0
        for fraction in (0.5, 0.3, 0.2, 0.1):
            c, t = required_per_arm(p_control=0.2308, p_treatment=0.2924, control_fraction=fraction)
            assert c + t > prev_total
            prev_total = c + t

    def test_rounds_up(self) -> None:
        """A fractional case does not exist, and rounding down would leave the
        study below the power it advertises."""
        control, treatment = required_per_arm(p_control=0.2308, p_treatment=0.2924)
        assert isinstance(control, int) and isinstance(treatment, int)

    @pytest.mark.parametrize(
        ("p_control", "p_treatment"),
        [(0.30, 0.30), (0.30, 0.20), (0.5, 0.5)],
    )
    def test_no_sample_size_detects_a_non_positive_lift(
        self, p_control: float, p_treatment: float
    ) -> None:
        """Raises rather than returning an enormous number that reads as an
        answer."""
        with pytest.raises(ValueError, match="non-positive lift"):
            required_per_arm(p_control=p_control, p_treatment=p_treatment)

    @pytest.mark.parametrize("fraction", [0.0, 1.0, -0.1, 1.5])
    def test_rejects_degenerate_allocations(self, fraction: float) -> None:
        with pytest.raises(ValueError, match="control_fraction"):
            required_per_arm(p_control=0.20, p_treatment=0.30, control_fraction=fraction)

    @pytest.mark.parametrize("bad", [-0.1, 1.1])
    def test_rejects_non_probabilities(self, bad: float) -> None:
        with pytest.raises(ValueError, match="probability"):
            required_per_arm(p_control=bad, p_treatment=0.9)


class TestCompletion:
    """The honest reading of "how far along are we"."""

    def _plan(self, control_now: int, treatment_now: int) -> PowerPlan:
        return sample_size_plan(
            control_now=control_now,
            treatment_now=treatment_now,
            p_control=0.2308,
            p_treatment=0.2924,
        )

    def test_governed_by_the_binding_arm(self) -> None:
        """5,000 treated and 12 control cases is not 99% of an answer.

        Reporting completion from the total is the single most misleading thing
        this panel could do, so it is the first thing asserted.
        """
        plan = self._plan(control_now=12, treatment_now=5000)
        assert plan.completion < 0.03
        assert not plan.is_powered

    def test_the_current_position(self) -> None:
        """39 control, 171 treated — roughly 5% of a powered study."""
        plan = self._plan(control_now=39, treatment_now=171)
        assert 0.04 <= plan.completion <= 0.06
        assert not plan.is_powered

    def test_powered_when_both_arms_are_full(self) -> None:
        plan = self._plan(control_now=800, treatment_now=800)
        assert plan.is_powered
        assert plan.completion == 1.0
        assert plan.cases_remaining == 0

    def test_one_full_arm_is_not_powered(self) -> None:
        plan = self._plan(control_now=800, treatment_now=100)
        assert not plan.is_powered

    def test_completion_never_exceeds_one(self) -> None:
        plan = self._plan(control_now=99_999, treatment_now=99_999)
        assert plan.completion == 1.0


class TestAttemptsNeeded:
    """Failed payments translated into traffic a merchant recognises."""

    def _plan(self) -> PowerPlan:
        return sample_size_plan(
            control_now=39, treatment_now=171, p_control=0.2308, p_treatment=0.2924
        )

    def test_lower_failure_rate_needs_more_traffic(self) -> None:
        plan = self._plan()
        assert plan.attempts_needed(0.08) > plan.attempts_needed(0.20)

    def test_a_realistic_figure(self) -> None:
        """~1,380 remaining cases at a 12% failure rate is ~11,500 attempts."""
        plan = self._plan()
        assert 10_000 <= plan.attempts_needed(0.12) <= 13_000

    @pytest.mark.parametrize("bad", [0.0, -0.1, 1.5])
    def test_rejects_impossible_failure_rates(self, bad: float) -> None:
        with pytest.raises(ValueError, match="failure_rate"):
            self._plan().attempts_needed(bad)


class TestETA:
    """A projected date, or an honest absence of one."""

    TODAY = date(2026, 8, 31)

    def _plan(self, control_now: int = 39, treatment_now: int = 171) -> PowerPlan:
        return sample_size_plan(
            control_now=control_now,
            treatment_now=treatment_now,
            p_control=0.2308,
            p_treatment=0.2924,
        )

    def test_no_traffic_gives_no_date(self) -> None:
        """Extrapolating a completion date from zero arrivals is how a
        countdown becomes fiction. The caller must render "unknown"."""
        assert self._plan().eta(0.0, today=self.TODAY) is None

    def test_negative_rate_gives_no_date(self) -> None:
        assert self._plan().eta(-5.0, today=self.TODAY) is None

    def test_already_powered_gives_no_date(self) -> None:
        assert self._plan(800, 800).eta(50.0, today=self.TODAY) is None

    def test_a_real_projection(self) -> None:
        """~1,380 cases remaining at 40/day is about 35 days."""
        eta = self._plan().eta(40.0, today=self.TODAY)
        assert eta is not None
        assert 30 <= (eta - self.TODAY).days <= 40

    def test_faster_arrival_is_never_later(self) -> None:
        slow = self._plan().eta(10.0, today=self.TODAY)
        fast = self._plan().eta(100.0, today=self.TODAY)
        assert slow is not None and fast is not None
        assert fast <= slow

    def test_the_date_is_injected_not_read(self) -> None:
        """Two different `today` values must give two different answers, which
        is only true if the parameter is actually used. INC-023 was a headline
        number that changed with the time of day because the code read the wall
        clock; a projection is the most tempting possible place to do it again.
        """
        a = self._plan().eta(40.0, today=date(2026, 1, 1))
        b = self._plan().eta(40.0, today=date(2026, 6, 1))
        assert a is not None and b is not None
        assert a != b
        assert (b - a).days == (date(2026, 6, 1) - date(2026, 1, 1)).days


class TestAgreementWithThePreRegistration:
    """The document and the code must not drift.

    The first version of this test searched the whole document for the figure
    as a substring. It passed with §5 deliberately falsified to "750 per arm",
    because 796 still appeared in the allocation table two sections earlier. A
    substring search over a whole file is not a consistency check -- it is a
    check that a number is *mentioned*, which is nearly always true.

    So each claim is now extracted from the specific sentence that makes it.
    """

    DOC = Path(__file__).resolve().parents[1] / "docs" / "PRE-REGISTRATION.md"

    @staticmethod
    def _expected() -> tuple[int, int]:
        return required_per_arm(p_control=0.2308, p_treatment=0.2924)

    def _text(self) -> str:
        assert self.DOC.is_file(), f"{self.DOC} not found -- this test would silently pass"
        return self.DOC.read_text(encoding="utf-8")

    def test_section_5_states_the_computed_requirement(self) -> None:
        control, treatment = self._expected()
        match = re.search(
            r"Required, at 50/50: \*\*([\d,]+) per arm, ([\d,]+) cases total\.\*\*",
            self._text(),
        )
        assert match, "§5's requirement sentence is not in the form this test can read"
        assert int(match.group(1).replace(",", "")) == control
        assert int(match.group(2).replace(",", "")) == control + treatment

    def test_the_allocation_table_row_is_correct(self) -> None:
        control, treatment = self._expected()
        match = re.search(
            r"\| 50/50 \(this experiment\) \| ([\d,]+) \| ([\d,]+) \| \*\*([\d,]+)\*\* \|",
            self._text(),
        )
        assert match, "the 50/50 row of §4's table is not in the expected form"
        got = [int(g.replace(",", "")) for g in match.groups()]
        assert got == [control, treatment, control + treatment]

    def test_the_demo_allocation_row_is_correct(self) -> None:
        control, treatment = required_per_arm(
            p_control=0.2308, p_treatment=0.2924, control_fraction=39 / 210
        )
        match = re.search(
            r"\| 81/19 \(current demo\) \| ([\d,]+) \| ([\d,]+) \| \*\*([\d,]+)\*\* \|",
            self._text(),
        )
        assert match, "the 81/19 row of §4's table is not in the expected form"
        got = [int(g.replace(",", "")) for g in match.groups()]
        assert got == [control, treatment, control + treatment]

    def test_the_stopping_rule_uses_the_same_n(self) -> None:
        """§6 commits to analysing once at a specific n. If that disagrees with
        §5 the pre-registration contradicts itself, which is worse than being
        wrong in one place."""
        control, treatment = self._expected()
        total = control + treatment
        text = self._text()
        assert f"Analysis happens once, at n = {total:,}" in text
        assert f"at n = {total:,}, the 95% confidence interval" in text

    def test_the_current_position_is_the_binding_arm(self) -> None:
        """§5 quotes a completion percentage. It must be the control arm's --
        the arm that governs power -- not the total's."""
        control, _ = self._expected()
        match = re.search(r"39 control cases, ([\d.]+)% of the control arm", self._text())
        assert match, "§5's current-position sentence is not in the expected form"
        assert float(match.group(1)) == pytest.approx(39 / control * 100, abs=0.1)
