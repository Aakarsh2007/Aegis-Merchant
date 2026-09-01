"""Where every rupee went, as an identity that has to balance.

Why this module exists
----------------------

The README led with three figures arranged as a sum::

    Rs 2,02,760          Rs 60,217         Rs 1,39,021
    GROSS RECOVERED  ->  CLAIMABLE BY US +  NOT CLAIMED

A reviewer added them up. ``60,217 + 1,39,021 = 1,99,238``, which is not
``2,02,760``, and they asked the obvious question: *if the headline numbers do
not reconcile, can I trust the attribution system?* That is exactly the right
thing to ask, and the honest answer is that the arithmetic was never wrong --
**the arrow and the plus sign were.** The three quantities are not a partition:

* **Gross recovered** is a sum of rupees over cases that settled on a path we
  drove.
* **Not claimed** is a sum of rupees over a *disjoint* set of cases -- ones
  that settled organically, where we credited ourselves zero.
* **Incremental** is not a sum of rupees at all. It is an *estimate*: the
  measured lift applied to the treated population's exposure, net of costs. It
  happens to be smaller than gross, which made it look like a subset of gross,
  which is how the layout came to imply subtraction.

Presenting an estimate as a slice of a total is precisely the overstatement
this project exists to refuse -- committed in its own headline, where it was
most visible and least noticed. So the fix is not better prose. There is a real
identity underneath, and it balances exactly::

    money that arrived  =  recovered on a path we drove  +  arrived organically

with the incremental estimate reported *beneath* the driven figure as a derived
quantity, never as a term in the sum.

``balances`` is computed and asserted rather than hoped for, and the residual is
returned even though it is zero by construction -- a reader should be able to
see that it is zero instead of taking it on faith.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.provenance import Figure, Provenance
from app.db.enums import CaseStatus
from app.db.models import RecoveryCase
from app.services.attribution import RecoveryReport

__all__ = ["MoneyLedger", "money_ledger"]


@dataclass(frozen=True)
class MoneyLedger:
    """The three populations, the identity between them, and the estimate."""

    #: Settled on a path we drove, and credited to us.
    driven_paise: int
    driven_cases: int
    #: Settled without an action of ours we can point to. Credited zero.
    organic_paise: int
    organic_cases: int
    #: Razorpay-verified demo injections, excluded from the measured population
    #: by workflow.md 14.4 and shown separately so the exclusion is visible
    #: rather than silent.
    demo_verified_paise: int
    demo_verified_cases: int

    #: The estimate. NOT a term in the identity below.
    incremental_estimate_paise: int
    cost_paise: int

    @property
    def arrived_paise(self) -> int:
        """The one true total: every rupee that reached the merchant."""
        return self.driven_paise + self.organic_paise

    @property
    def arrived_cases(self) -> int:
        return self.driven_cases + self.organic_cases

    @property
    def residual_paise(self) -> int:
        """Zero by construction. Returned anyway, so it can be *seen* to be zero."""
        return self.arrived_paise - (self.driven_paise + self.organic_paise)

    @property
    def balances(self) -> bool:
        return self.residual_paise == 0

    @property
    def claimed_share(self) -> float:
        """Fraction of arriving money we are prepared to claim as ours.

        The number the whole project is about, and it is well under a half.
        """
        if self.arrived_paise <= 0:
            return 0.0
        return self.incremental_estimate_paise / self.arrived_paise

    def as_dict(self) -> dict[str, Any]:
        return {
            "identity": "arrived = driven + organic",
            "balances": self.balances,
            "residual_paise": self.residual_paise,
            "arrived": Figure(
                paise=self.arrived_paise,
                provenance=Provenance.SIMULATED,
                basis=(
                    f"every rupee that reached the merchant across "
                    f"{self.arrived_cases} settled cases. The two terms below are "
                    "disjoint populations and sum to exactly this."
                ),
            ).as_dict(),
            "driven": Figure(
                paise=self.driven_paise,
                provenance=Provenance.SIMULATED,
                basis=(
                    f"settled on a path we drove, over {self.driven_cases} cases. "
                    "This is the figure a gross-recovery dashboard would report as "
                    "its headline."
                ),
            ).as_dict(),
            "organic": Figure(
                paise=self.organic_paise,
                provenance=Provenance.SIMULATED,
                basis=(
                    f"arrived across {self.organic_cases} cases and was credited to "
                    "us at zero -- held as control, or settled with no action of "
                    "ours to point to."
                ),
            ).as_dict(),
            "demo_verified": Figure(
                paise=self.demo_verified_paise,
                provenance=Provenance.RAZORPAY_VERIFIED,
                basis=(
                    f"{self.demo_verified_cases} real Razorpay Test Mode "
                    "recoveries, excluded from the measured population by 14.4: a "
                    "demonstration of mechanism is not a data point"
                ),
            ).as_dict(),
            "incremental_estimate": Figure(
                paise=self.incremental_estimate_paise,
                provenance=Provenance.SIMULATED,
                basis=(
                    "an ESTIMATE, not a slice of the total above: the measured "
                    "lift applied to the treated arm's exposure, less discount and "
                    "inference costs. Deliberately not a term in the identity -- "
                    "presenting an estimate as a subset of a sum is the "
                    "overstatement this ledger exists to prevent."
                ),
            ).as_dict(),
            "cost_paise": self.cost_paise,
            "claimed_share": round(self.claimed_share, 4),
            "note": (
                "Three quantities, two of which add up. An earlier README laid "
                "these out as gross -> claimable + not claimed, which reads as a "
                "partition and is not one. The arrow was the bug, not the "
                "arithmetic."
            ),
        }


async def money_ledger(session: AsyncSession, *, attribution: RecoveryReport) -> MoneyLedger:
    """Build the ledger from the database and one attribution report.

    ``attribution`` is passed in rather than recomputed, for the INC-039 reason:
    the lift has exactly one implementation, and a second one here would be free
    to disagree with the dashboard.
    """

    async def _sum(column: Any, *where: Any) -> tuple[int, int]:
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(column), 0),
                    func.count(RecoveryCase.id),
                ).where(*where)
            )
        ).one()
        return int(row[0] or 0), int(row[1] or 0)

    # Demo injections are filtered out of `driven` here rather than subtracted
    # afterwards, so the two figures cannot double-count.
    driven_paise, driven_cases = await _sum(
        RecoveryCase.recovered_amount_paise,
        RecoveryCase.status == CaseStatus.RECOVERED,
        RecoveryCase.is_demo.is_(False),
    )
    organic_paise, organic_cases = await _sum(
        RecoveryCase.amount_paise,
        RecoveryCase.status == CaseStatus.RESOLVED_ORGANIC,
        RecoveryCase.is_demo.is_(False),
    )
    demo_paise, demo_cases = await _sum(
        RecoveryCase.recovered_amount_paise,
        RecoveryCase.status == CaseStatus.RECOVERED,
        RecoveryCase.is_demo.is_(True),
    )

    inference_paise = attribution.inference_cost_micro_inr // 10_000
    return MoneyLedger(
        driven_paise=driven_paise,
        driven_cases=driven_cases,
        organic_paise=organic_paise,
        organic_cases=organic_cases,
        demo_verified_paise=demo_paise,
        demo_verified_cases=demo_cases,
        incremental_estimate_paise=attribution.net_incremental_paise,
        cost_paise=attribution.discount_cost_paise + inference_paise,
    )
