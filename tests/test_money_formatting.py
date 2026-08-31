"""Money renders with two decimals, everywhere, in every layer.

Three separate places got this wrong and each was found by eye rather than by a
test: `Rs 20,055.6` on an approval card (a human was being asked to authorise
that figure), and `Rs 7.0` / `Rs 28.0` in the cost panel's rate text.

The cause is the same each time: a formatter that is correct for a *quantity*
is wrong for *money*. `toLocaleString` drops a trailing zero; so does `str()`
on a float. Neither is a bug in isolation, which is why this needs a check that
looks at rendered output rather than at any one call site.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.core.provenance import rupees

ROOT = Path(__file__).resolve().parents[1]

#: `Rs 1.5` -- exactly one digit after the point, not followed by another.
ONE_DECIMAL = re.compile(r"Rs\s[\d,]+\.\d(?!\d)")


class TestTheRupeeFormatter:
    """`rupees()` is the Python-side source of truth."""

    @pytest.mark.parametrize(
        ("paise", "expected"),
        [
            (0, "0.00"),
            (100, "1.00"),
            (2005560, "20,05,560".replace("20,05,560", "20,055.60")),
            (20275995, "2,02,759.95"),
            (5, "0.05"),
            (50, "0.50"),
            (-100, "-1.00"),
        ],
    )
    def test_always_two_decimals(self, paise: int, expected: str) -> None:
        assert rupees(paise) == expected

    def test_a_trailing_zero_is_never_dropped(self) -> None:
        """The exact failure mode: 2005560 paise is Rs 20,055.60, not .6"""
        assert rupees(2_005_560) == "20,055.60"
        assert not ONE_DECIMAL.match(f"Rs {rupees(2_005_560)}")

    @pytest.mark.parametrize("paise", [1, 10, 100, 1000, 999_999_999])
    def test_output_is_never_one_decimal(self, paise: int) -> None:
        assert not ONE_DECIMAL.search(f"Rs {rupees(paise)}")

    def test_indian_grouping(self) -> None:
        """Lakh grouping, not thousands. Formatting an Indian merchant's revenue
        with Western grouping reads as not having thought about the market."""
        assert rupees(20_275_995) == "2,02,759.95"


class TestNoBareFloatMoneyInTheSource:
    """A rate constant interpolated raw renders `Rs 7.0`.

    Scoped to the two known-risky patterns rather than all f-strings: a broad
    scan would flag every legitimate use and get disabled within a week.
    """

    def test_rate_constants_are_formatted(self) -> None:
        source = (ROOT / "apps/api/app/services/metrics.py").read_text(encoding="utf-8")
        for name in ("_INR_PER_MILLION_INPUT_TOKENS", "_INR_PER_MILLION_OUTPUT_TOKENS"):
            bare = f"{{{name}}}"
            assert bare not in source, (
                f"{name} is interpolated without a format spec, which renders "
                f"'Rs 7.0'. Use {{{name}:.2f}}."
            )


class TestTheFrontendUsesAMinimumFractionDigits:
    """`toLocaleString` drops a trailing zero unless told not to.

    Reads the components because there is no way to assert on rendered React
    from pytest, and the alternative -- trusting that every future money render
    remembers the option -- is what produced the bug.
    """

    COMPONENTS = ROOT / "apps/web/src/components"

    def test_the_components_directory_exists(self) -> None:
        assert self.COMPONENTS.is_dir(), "this test would silently stop checking"

    def test_the_shared_helper_sets_the_option(self) -> None:
        """**The check the first version of this test was missing.**

        Routing every component through a shared `rupees()` left nothing for the
        component scan below to find, so deleting the option from the helper
        passed all seventeen tests. The refactor hollowed out its own guard --
        the INC-006 pattern, appearing inside a fix for the same pattern for the
        second time in this project.

        Now the helper is checked directly, and the scan below catches anyone
        who bypasses it.
        """
        source = (ROOT / "apps/web/src/lib/api.ts").read_text(encoding="utf-8")
        match = re.search(
            r"export function rupees\(paise: number\): string \{(.*?)\n\}",
            source,
            re.DOTALL,
        )
        assert match, "the shared rupees() helper is not where this test expects it"
        body = match.group(1)
        assert "minimumFractionDigits: 2" in body, (
            "the shared money formatter drops trailing zeros, so every rupee "
            "figure in the UI can render as `Rs 20,055.6`"
        )
        assert "maximumFractionDigits: 2" in body
        assert '"en-IN"' in body, "Indian digit grouping, not Western"

    def test_every_paise_division_sets_minimum_fraction_digits(self) -> None:
        offenders: list[str] = []
        for path in sorted(self.COMPONENTS.glob("*.tsx")):
            text = path.read_text(encoding="utf-8")
            # A money render divides paise by 100 and localises it.
            for match in re.finditer(r"_paise\s*/\s*100\)\.toLocaleString\(([^)]*)\)", text):
                if "minimumFractionDigits" not in match.group(1):
                    offenders.append(f"{path.name}: {match.group(0)[:70]}")
        assert not offenders, (
            "money rendered without minimumFractionDigits, so a trailing zero "
            "will be dropped:\n  " + "\n  ".join(offenders)
        )
