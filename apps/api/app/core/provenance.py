"""Every number carries where it came from (§14.5).

The design rule in the workflow says *"every rupee figure renders with a
provenance badge"* and calls it a UI constraint. Left there it would be a
convention, and conventions are followed until the afternoon someone adds a
tile in a hurry. This module makes it a type: :class:`Figure` cannot be
constructed without a :class:`Provenance`, so a number that reaches the
dashboard without one is a compile-time impossibility rather than a review
comment.

Why this matters more than it sounds
------------------------------------

The single most damaging thing this project could do is put ₹2,02,760 on a
screen next to ₹60,217 without saying that the first is what a dashboard would
show and the second is the lift *estimated* against a holdout under a declared
response model. Both numbers are true. They answer different questions, and a
viewer who cannot tell which is which will take the larger one.

Note the wording, because it is the point of the module: **"estimated causal
lift", never "what we caused"**. The machinery that computes it is real and
unmodified, and the customer responses it runs against are a parameter we
declared. A simulation cannot establish causation, and a docstring in the file
whose job is honest labelling is a poor place to imply otherwise.

Three levels, and the distinction between them is the whole point:

``RAZORPAY_VERIFIED``
    A signed webhook from Razorpay says this money moved. The strongest claim
    available, and the only one we make unqualified.

``SIMULATED``
    Computed from the seeded corpus. The *machinery* is real — the same
    attribution rules, the same arm assignment, the same arithmetic — but the
    customer responses are a declared parameter, not observed behaviour.

``ESTIMATED``
    A projection or a model output. Inference cost at published paid rates,
    when actual spend is ₹0, is the canonical example.

No tile mixes provenance. A figure that would need two badges is two figures,
because averaging a verified number with a simulated one produces something
that is neither and is labelled as whichever the author preferred.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

__all__ = ["Count", "Figure", "Provenance", "rupees"]


class Provenance(StrEnum):
    """Where a number came from. Rendered as a badge, verbatim."""

    #: A signed Razorpay webhook proves it.
    RAZORPAY_VERIFIED = "RAZORPAY_VERIFIED"
    #: Real machinery, seeded inputs.
    SIMULATED = "SIMULATED"
    #: A projection at published rates, or a model output.
    ESTIMATED = "ESTIMATED"


def rupees(paise: int) -> str:
    """Render paise as rupees, Indian digit grouping.

    ``12345678`` becomes ``"1,23,456.78"`` — lakh grouping, not thousands.
    Formatting an Indian merchant's revenue with Western grouping is a small
    thing that reads as not having thought about the market.
    """
    negative = paise < 0
    whole, frac = divmod(abs(paise), 100)
    digits = str(whole)
    if len(digits) > 3:
        head, tail = digits[:-3], digits[-3:]
        parts: list[str] = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        digits = ",".join([*parts, tail])
    return f"{'-' if negative else ''}{digits}.{frac:02d}"


@dataclass(frozen=True)
class Figure:
    """A rupee amount that knows where it came from.

    ``basis`` is a one-line statement of how the number was produced, shown on
    hover. A badge tells the viewer how much to trust a figure; the basis tells
    them why, and is what makes the badge checkable rather than decorative.
    """

    paise: int
    provenance: Provenance
    basis: str

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise ValueError("a Figure must state its basis: an unexplained badge is decoration")

    @property
    def display(self) -> str:
        return f"Rs {rupees(self.paise)}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "paise": self.paise,
            "display": self.display,
            "provenance": self.provenance.value,
            "basis": self.basis,
        }


@dataclass(frozen=True)
class Count:
    """A non-money quantity. Also badged.

    Counts mislead the same way amounts do: "31 recoveries" invites the same
    question as "₹1.24L recovered", and the answer has the same three levels.
    """

    value: int
    provenance: Provenance
    basis: str

    def __post_init__(self) -> None:
        if not self.basis.strip():
            raise ValueError("a Count must state its basis")

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "provenance": self.provenance.value,
            "basis": self.basis,
        }
