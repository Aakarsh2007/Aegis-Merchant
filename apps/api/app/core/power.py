"""How many cases the causal question actually needs.

The three numbers on the dashboard answer three different questions. ₹1.00
`RAZORPAY_VERIFIED` answers *"can this execute and verify a recovery through
Razorpay"* — yes. ₹60,217 `SIMULATED` answers *"what might it recover at
scale"* — a declared model, not evidence. Neither answers **"did RevPilot cause
additional customers to pay"**, and this module exists to say precisely how far
away that answer is instead of leaving it as a caveat in prose.

A gap stated as a number is a different object from a gap stated as a
disclaimer. "Not statistically significant" invites the reader to wonder how
close we are; "39 of the 796 control cases a 50/50 design needs, 4.9%" does
not. The whole design commitment is in `docs/PRE-REGISTRATION.md`, committed
before any live data existed; this is the arithmetic behind §5 of it, in code
so it can be checked and so the dashboard can show a completion percentage
rather than an adjective.

Why the balanced split matters
------------------------------

Power is governed by the **smaller** arm, so an unbalanced design wastes the
larger one. At the demo's 19% holdout the experiment needs 2,504 cases; at
50/50 it needs 1,592 — the same answer for 36.4% fewer failed payments. That is
not a free win: the cost is real and falls on the merchant, who forgoes
recovery on half of all recoverable cases for the duration. The trade is theirs
to make, which is why `control_fraction` is a parameter here rather than a
constant.

No SciPy
--------

The normal quantiles are hard-coded for the two levels this project uses. A
dependency for two numbers that have not changed since 1908 would be a poor
trade, and the values are asserted against a table in the tests.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final

__all__ = [
    "Z_ALPHA_05",
    "Z_POWER_80",
    "PowerPlan",
    "required_per_arm",
    "sample_size_plan",
]

#: Two-sided alpha = 0.05. The same 1.96 as `stats.Z_95`, named for its role here:
#: this one is a test's rejection threshold, not an interval's width, and a
#: future change to one must not silently change the other.
Z_ALPHA_05: Final = 1.959964

#: 80% power. The conventional floor — below this a null result says more about
#: the sample than about the effect.
Z_POWER_80: Final = 0.841621


def required_per_arm(
    *,
    p_control: float,
    p_treatment: float,
    control_fraction: float = 0.5,
    z_alpha: float = Z_ALPHA_05,
    z_power: float = Z_POWER_80,
) -> tuple[int, int]:
    """Cases needed per arm to detect this lift, at this allocation.

    Returns ``(control, treatment)``, both rounded **up**: a fractional case
    does not exist, and rounding down would leave the study below the power it
    claims.

    The variance of the difference between two proportions is
    ``p_c(1-p_c)/n_c + p_t(1-p_t)/n_t``. Fixing the allocation ratio
    ``k = n_t/n_c`` lets that collapse to a single unknown, which is why an
    unbalanced design needs a larger total: the ``/k`` term shrinks the
    treatment arm's contribution to precision, and the control arm has to make
    up the whole difference alone.

    Raises for a zero or negative lift. There is no sample size that detects an
    effect of zero, and returning a very large number would look like an answer.
    """
    if not 0.0 < control_fraction < 1.0:
        raise ValueError(f"control_fraction must be in (0.0, 1.0), got {control_fraction}")
    for name, p in (("p_control", p_control), ("p_treatment", p_treatment)):
        if not 0.0 <= p <= 1.0:
            raise ValueError(f"{name} must be a probability, got {p}")

    delta = p_treatment - p_control
    if delta <= 0:
        raise ValueError(
            f"no sample size detects a non-positive lift (control {p_control}, "
            f"treatment {p_treatment}); state the effect you are looking for"
        )

    k = (1.0 - control_fraction) / control_fraction  # treatment per control
    var_c = p_control * (1.0 - p_control)
    var_t = p_treatment * (1.0 - p_treatment)

    # The standard error the test must achieve to separate `delta` from zero at
    # this alpha and this power.
    se_target = delta / (z_alpha + z_power)
    n_control = (var_c + var_t / k) / (se_target**2)

    return math.ceil(n_control), math.ceil(k * n_control)


@dataclass(frozen=True)
class PowerPlan:
    """Where the experiment stands against what it needs.

    Deliberately carries both arms' *current* and *required* sizes rather than
    a single percentage. A completion figure computed from the total would read
    as 13% when the control arm — the one that governs power — is at 4.9%, and
    the optimistic reading is the wrong one to make easy.
    """

    control_now: int
    treatment_now: int
    control_required: int
    treatment_required: int
    p_control: float
    p_treatment: float
    control_fraction: float

    @property
    def completion(self) -> float:
        """Fraction of the way to a powered study, governed by the binding arm.

        The *minimum* of the two ratios, not the mean and not the total: a
        study with 5,000 treated and 12 control cases is not 99% of the way to
        an answer.
        """
        if self.control_required <= 0 or self.treatment_required <= 0:
            return 0.0
        return min(
            1.0,
            min(
                self.control_now / self.control_required,
                self.treatment_now / self.treatment_required,
            ),
        )

    @property
    def is_powered(self) -> bool:
        return (
            self.control_now >= self.control_required
            and self.treatment_now >= self.treatment_required
        )

    @property
    def cases_remaining(self) -> int:
        return max(0, self.control_required - self.control_now) + max(
            0, self.treatment_required - self.treatment_now
        )

    def attempts_needed(self, failure_rate: float) -> int:
        """Payment *attempts* implied by the remaining cases.

        The number a merchant recognises. 1,592 failed payments is abstract;
        at a 12% failure rate it is 13,267 checkout attempts, which is
        a quantity they can compare against their own traffic.
        """
        if not 0.0 < failure_rate <= 1.0:
            raise ValueError(f"failure_rate must be in (0.0, 1.0], got {failure_rate}")
        return math.ceil(self.cases_remaining / failure_rate)

    def eta(self, cases_per_day: float, *, today: date) -> date | None:
        """When the study completes at this arrival rate.

        ``None`` when the rate is zero or the study is already powered —
        the caller must render "unknown" rather than a date, because
        extrapolating a completion date from no traffic is how a countdown
        becomes fiction. ``today`` is injected: reading the wall clock here
        would make the projection drift with the time of day, which is INC-023.
        """
        if self.is_powered or cases_per_day <= 0:
            return None
        return today + timedelta(days=math.ceil(self.cases_remaining / cases_per_day))


def sample_size_plan(
    *,
    control_now: int,
    treatment_now: int,
    p_control: float,
    p_treatment: float,
    control_fraction: float = 0.5,
) -> PowerPlan:
    """Assemble a :class:`PowerPlan` for the current position."""
    control_required, treatment_required = required_per_arm(
        p_control=p_control,
        p_treatment=p_treatment,
        control_fraction=control_fraction,
    )
    return PowerPlan(
        control_now=control_now,
        treatment_now=treatment_now,
        control_required=control_required,
        treatment_required=treatment_required,
        p_control=p_control,
        p_treatment=p_treatment,
        control_fraction=control_fraction,
    )
