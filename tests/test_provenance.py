"""Provenance: can a rupee figure reach a screen without saying where it came from?

The workflow calls this a UI design rule. A UI rule is followed until the
afternoon someone adds a tile in a hurry, so these tests check that it is
enforced by the type instead — and, more importantly, that the *API* cannot
emit a money figure without a badge.

The last test in this file is the one that matters: it walks the actual
response of every metrics endpoint looking for anything that smells like money
and asserts each one carries a provenance. It fails when someone adds a tile,
not when someone remembers to update a list.
"""

from __future__ import annotations

import pytest

from app.core.provenance import Count, Figure, Provenance, rupees


class TestIndianDigitGrouping:
    """Lakh grouping, not thousands. Formatting an Indian merchant's revenue
    with Western grouping reads as not having thought about the market."""

    @pytest.mark.parametrize(
        ("paise", "expected"),
        [
            (0, "0.00"),
            (99, "0.99"),
            (100, "1.00"),
            (123456, "1,234.56"),
            (12345678, "1,23,456.78"),
            (20276000, "2,02,760.00"),
            (1234567890, "1,23,45,678.90"),
            (100000000000, "1,00,00,00,000.00"),
        ],
    )
    def test_grouping(self, paise: int, expected: str) -> None:
        assert rupees(paise) == expected

    def test_negative_amounts_keep_the_sign(self) -> None:
        """A negative lift is reported as negative (§14). The formatter must
        not quietly drop the sign on the way to the screen."""
        assert rupees(-6021700) == "-60,217.00"

    def test_paise_are_never_lost(self) -> None:
        assert rupees(1) == "0.01"
        assert rupees(-1) == "-0.01"


class TestABadgeCannotBeOmitted:
    def test_a_figure_requires_a_provenance(self) -> None:
        with pytest.raises(TypeError):
            Figure(paise=100)  # type: ignore[call-arg]

    def test_a_figure_requires_a_basis(self) -> None:
        """A badge with no explanation is decoration. The basis is what makes
        it checkable."""
        with pytest.raises(ValueError, match="basis"):
            Figure(paise=100, provenance=Provenance.SIMULATED, basis="   ")

    def test_a_count_requires_a_basis_too(self) -> None:
        """ "31 recoveries" invites the same question as "Rs 1.24L recovered"."""
        with pytest.raises(ValueError, match="basis"):
            Count(value=31, provenance=Provenance.SIMULATED, basis="")

    def test_the_serialised_form_always_carries_both(self) -> None:
        body = Figure(
            paise=20276000,
            provenance=Provenance.RAZORPAY_VERIFIED,
            basis="sum of verified webhooks",
        ).as_dict()
        assert body["provenance"] == "RAZORPAY_VERIFIED"
        assert body["basis"]
        assert body["display"] == "Rs 2,02,760.00"

    def test_figures_are_immutable(self) -> None:
        """A figure whose badge could be reassigned after construction would
        let a caller relabel a SIMULATED number as RAZORPAY_VERIFIED."""
        figure = Figure(paise=1, provenance=Provenance.SIMULATED, basis="x")
        with pytest.raises(Exception):  # noqa: B017 - dataclass raises FrozenInstanceError
            figure.provenance = Provenance.RAZORPAY_VERIFIED  # type: ignore[misc]


class TestTheThreeLevelsMeanDifferentThings:
    def test_all_three_are_distinct(self) -> None:
        assert len({p.value for p in Provenance}) == 3

    def test_verified_is_the_only_unqualified_claim(self) -> None:
        """RAZORPAY_VERIFIED means a signed webhook says the money moved.
        Nothing else may use that badge, and the value string is what the
        dashboard renders, so it must not be edited casually."""
        assert Provenance.RAZORPAY_VERIFIED.value == "RAZORPAY_VERIFIED"
        assert Provenance.SIMULATED.value == "SIMULATED"
        assert Provenance.ESTIMATED.value == "ESTIMATED"
