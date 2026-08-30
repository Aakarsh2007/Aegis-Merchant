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
from typing import Final

__all__ = ["Z_95", "wilson_bounds", "wilson_lower"]

#: 95% two-sided.
Z_95: Final = 1.96


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
