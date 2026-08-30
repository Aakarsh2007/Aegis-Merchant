"""Experiment arm assignment (workflow.md §14.2).

**We deliberately do not act on ~18% of eligible cases.** That is not a bug, a
throttle, or a safety margin — it is the only thing that makes the headline
recovery number falsifiable. Without a control group, "we recovered ₹1.24L" is
unanswerable to the obvious question: *how do you know they would not have paid
anyway?*

Three properties the assignment must have, and each one rules out an easier
implementation:

**Deterministic from the case identity.** Not ``random.random()``. The arm must
be stable across a process restart, a webhook redelivery, and a replay of the
batch — otherwise a case could be observed as control on Monday and treated on
Tuesday, and the measurement would be of nothing.

**Independent of anything correlated with outcome.** The hash covers the case's
identity and nothing else: not amount, not LTV, not playbook. Assigning by
amount would put the easy recoveries in one arm and make the lift a measurement
of the split rather than of the intervention.

**Immutable once written.** ``experiment_assignments`` has the case as its
primary key. Re-assigning a case mid-flight would be the cleanest possible way
to manufacture a favourable result, so the schema makes it impossible rather
than merely discouraged.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from app.db.enums import ExperimentArm

__all__ = ["Assignment", "assign_arm"]


@dataclass(frozen=True)
class Assignment:
    arm: ExperimentArm
    #: The hash prefix, stored so an auditor can recompute the assignment and
    #: confirm it was not chosen after the fact.
    assignment_hash: str
    experiment_key: str
    #: The [0,1) position this case landed on. Below the control fraction means
    #: control; recorded so the split can be checked for drift.
    position: float

    @property
    def is_control(self) -> bool:
        return self.arm is ExperimentArm.CONTROL


def assign_arm(
    case_id: str,
    *,
    experiment_key: str,
    control_fraction: float,
) -> Assignment:
    """Assign a case to an arm, reproducibly.

    ``control_fraction`` of 0 disables the holdout entirely. That is allowed --
    a merchant may not want one -- but it makes every recovery figure a gross
    number with no counterfactual, and the API surfaces that rather than hiding
    it behind an unchanged label.
    """
    if not 0.0 <= control_fraction < 1.0:
        raise ValueError(f"control_fraction must be in [0.0, 1.0), got {control_fraction}")

    digest = hashlib.sha256(f"{experiment_key}:{case_id}".encode()).digest()
    # First 8 bytes as a big-endian integer, scaled into [0, 1). Uniform, and
    # cheap enough to recompute during an audit.
    position = int.from_bytes(digest[:8], "big") / 2**64
    arm = ExperimentArm.CONTROL if position < control_fraction else ExperimentArm.TREATMENT

    return Assignment(
        arm=arm,
        assignment_hash=digest.hex()[:16],
        experiment_key=experiment_key,
        position=position,
    )
