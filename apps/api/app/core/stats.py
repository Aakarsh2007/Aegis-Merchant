"""Small-sample statistics, shared.

Both the rail-health index and the attribution report need a confidence
interval on a proportion, and for the same reason: **the naive answer is
actively wrong at the sample sizes this system lives at.**

The normal approximation gives an interval of exactly ``[0, 0]`` for 0
successes in 3 trials — asserting certainty from three data points. It would
blacklist a healthy payment rail after one unlucky afternoon, and it would let
a recovery experiment claim a 0% control conversion from a handful of cases.
The Wilson interval is correctly uncertain in both places, needs no tuning
parameter, and stays inside [0, 1] by construction.

Lives in ``core`` rather than in either caller so the two cannot drift apart.
An earlier version had it only in ``rail_health``, and having the attribution
report import from the agent package would have been a strange dependency to
justify.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Final

__all__ = [
    "Z_95",
    "Z_95_EXACT",
    "TwoProportionTest",
    "normal_sf",
    "two_proportion_test",
    "wilson_bounds",
    "wilson_lower",
]

#: 95% two-sided.
Z_95: Final = 1.96

#: The same level to seven figures, for the interval on a *difference*.
#: `Z_95` stays at 1.96 because every Wilson figure this project has ever
#: published used it, and silently shifting them all to chase a fourth decimal
#: would be a change with no reader-visible benefit and a diff across every
#: recorded number. Kept separate and named, rather than reconciled.
Z_95_EXACT: Final = 1.959964


def wilson_bounds(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Returns ``(0.0, 1.0)`` — maximum uncertainty — when there are no trials,
    rather than dividing by zero or claiming a rate.
    """
    if trials <= 0:
        return 0.0, 1.0
    if successes < 0 or successes > trials:
        raise ValueError(f"successes={successes} out of range for trials={trials}")

    p = successes / trials
    z2 = z * z
    denom = 1 + z2 / trials
    centre = p + z2 / (2 * trials)
    margin = z * math.sqrt(p * (1 - p) / trials + z2 / (4 * trials * trials))
    return max(0.0, (centre - margin) / denom), min(1.0, (centre + margin) / denom)


def wilson_lower(successes: int, trials: int, z: float = Z_95) -> float:
    """The pessimistic end. Used for ranking, where a claim must be earned."""
    return wilson_bounds(successes, trials, z)[0]


@dataclass(frozen=True)
class TwoProportionTest:
    """The comparison the holdout exists to make, done properly.

    Reported alongside each arm's Wilson interval rather than instead of it,
    because they answer different questions and the difference is the one that
    matters. See :func:`two_proportion_test` for why this replaced an
    interval-overlap check.
    """

    #: Successes and trials, treatment then control.
    treated_paid: int
    treated_cases: int
    control_paid: int
    control_cases: int

    z: float
    p_value: float
    #: 95% interval on the *difference* in proportions, as a fraction.
    diff_ci95: tuple[float, float]

    @property
    def diff(self) -> float:
        """Treatment conversion minus control conversion.

        Signed. A negative lift is reported as negative -- a system that
        clamped this could not tell you it was making things worse.
        """
        if not self.treated_cases or not self.control_cases:
            return 0.0
        return self.treated_paid / self.treated_cases - self.control_paid / self.control_cases

    @property
    def is_significant(self) -> bool:
        """``p < 0.05`` on the pooled two-proportion z-test."""
        return self.p_value < 0.05

    @property
    def well_defined(self) -> bool:
        """False when either arm is empty, when no test is possible at all."""
        return self.treated_cases > 0 and self.control_cases > 0

    def as_dict(self) -> dict[str, float | bool | list[float]]:
        return {
            "z": round(self.z, 4),
            "p_value": round(self.p_value, 4),
            "diff": round(self.diff, 4),
            "diff_ci95": [round(b, 4) for b in self.diff_ci95],
            "is_significant": self.is_significant,
            "well_defined": self.well_defined,
        }


def normal_sf(z: float) -> float:
    """Upper-tail probability of the standard normal, via :func:`math.erfc`.

    ``erfc`` rather than ``1 - erf``: at the tails the subtraction loses every
    significant digit, and a p-value that silently becomes 0.0 is the kind of
    number a reader would take at face value. No SciPy, for the reason given in
    ``core.power``.
    """
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_proportion_test(
    *,
    treated_paid: int,
    treated_cases: int,
    control_paid: int,
    control_cases: int,
) -> TwoProportionTest:
    """Pooled two-sided two-proportion z-test, plus an interval on the difference.

    Why this exists
    ---------------

    Significance used to be decided by asking whether the two arms' Wilson
    intervals overlapped. That is a real test of *something*, but not of the
    hypothesis: non-overlap is strictly more conservative than a test on the
    difference, so the criterion could report "not significant" for a result
    that a correct test would reject. It answered a question nobody asked.

    A reviewer pushed on the wording and was right. "The confidence intervals
    overlap" is also a weak thing to say out loud when the honest sentence is
    available: *the observed lift is 6.16 percentage points, p = 0.44, which at
    this sample size is indistinguishable from chance.* One states a fact about
    two intervals; the other states the finding.

    The pooled standard error is used for the test statistic (the null says the
    two proportions are equal, so pooling is the right variance under it) and
    the unpooled one for the interval (which must not assume the null it is
    describing). Using one for both is a common and silent error.

    Degenerate inputs return a well-defined, non-significant result with
    ``well_defined=False`` rather than raising: an empty control arm is the
    normal state of a fresh clone, and it must render as "no test possible"
    rather than as a crash or, worse, as a pass.
    """
    if treated_cases < 0 or control_cases < 0 or treated_paid < 0 or control_paid < 0:
        raise ValueError("counts must be non-negative")
    if treated_paid > treated_cases or control_paid > control_cases:
        raise ValueError("successes cannot exceed trials")

    blank = TwoProportionTest(
        treated_paid=treated_paid,
        treated_cases=treated_cases,
        control_paid=control_paid,
        control_cases=control_cases,
        z=0.0,
        p_value=1.0,
        diff_ci95=(0.0, 0.0),
    )
    if treated_cases == 0 or control_cases == 0:
        return blank

    p_t = treated_paid / treated_cases
    p_c = control_paid / control_cases
    pooled = (treated_paid + control_paid) / (treated_cases + control_cases)
    se_pooled = math.sqrt(pooled * (1.0 - pooled) * (1.0 / treated_cases + 1.0 / control_cases))
    if se_pooled == 0.0:
        # Both arms all-paid or both all-unpaid: the difference is exactly zero
        # and there is nothing to test.
        return blank

    z = (p_t - p_c) / se_pooled
    se_unpooled = math.sqrt(p_t * (1.0 - p_t) / treated_cases + p_c * (1.0 - p_c) / control_cases)
    half = Z_95_EXACT * se_unpooled
    return TwoProportionTest(
        treated_paid=treated_paid,
        treated_cases=treated_cases,
        control_paid=control_paid,
        control_cases=control_cases,
        z=z,
        # Two-sided.
        p_value=min(1.0, 2.0 * normal_sf(abs(z))),
        diff_ci95=((p_t - p_c) - half, (p_t - p_c) + half),
    )
