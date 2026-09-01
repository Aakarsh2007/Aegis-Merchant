"""Figures the README quotes about the corpus must match the seed database.

The README claimed *"Revenue at risk ₹11,84,629"*. The real figure over the
committed corpus is ₹8,61,995 — the claim was stale by nearly 40%, left behind
by an earlier version of the seed, and it sat in the table a judge reads to
decide whether the numbers below it are trustworthy.

It was also mislabelled. *"Revenue at risk"* is the exact wording of a dashboard
tile that measures a **different** population — cases still open after the agent
has run, ₹6,64,067 over 117 — so a reader comparing the two saw the same words
against different numbers and had no way to tell which was wrong.

Both figures are legitimate. Neither was checkable. This file makes the corpus
half checkable, against the committed database rather than against a number
copied into a test.
"""

from __future__ import annotations

import re
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SEED = ROOT / "data" / "revpilot.seed.db"
README = ROOT / "README.md"

sys.path.insert(0, str(ROOT / "apps" / "api"))
from app.core.provenance import rupees  # noqa: E402


@pytest.fixture(scope="module")
def corpus() -> dict[str, int]:
    assert SEED.is_file(), f"{SEED} is committed and must exist"
    c = sqlite3.connect(f"file:{SEED}?mode=ro", uri=True)
    try:
        return {
            "attempts": c.execute("select count(*) from payment_attempts").fetchone()[0],
            "customers": c.execute("select count(*) from customers").fetchone()[0],
            "at_risk_paise": c.execute(
                "select sum(amount_paise) from payment_attempts "
                "where status in ('failed','abandoned')"
            ).fetchone()[0],
            "captured_paise": c.execute(
                "select sum(amount_paise) from payment_attempts where status='captured'"
            ).fetchone()[0],
        }
    finally:
        c.close()


def _readme() -> str:
    return README.read_text(encoding="utf-8")


class TestTheCorpusIsWhatTheReadmeSays:
    def test_attempt_count(self, corpus: dict[str, int]) -> None:
        assert f"**{corpus['attempts']}**" in _readme(), (
            f"the corpus has {corpus['attempts']} attempts"
        )

    def test_customer_count(self, corpus: dict[str, int]) -> None:
        assert f"| Customers | {corpus['customers']} " in _readme()

    def test_at_risk_matches_the_seed(self, corpus: dict[str, int]) -> None:
        """**The figure that was stale by 40%.**"""
        expected = rupees(corpus["at_risk_paise"]).split(".")[0]
        assert f"\u20b9{expected}" in _readme(), (
            f"the corpus's at-risk total is \u20b9{expected}; the README says something else"
        )

    def test_captured_gmv_matches_the_seed(self, corpus: dict[str, int]) -> None:
        expected = rupees(corpus["captured_paise"]).split(".")[0]
        assert f"\u20b9{expected}" in _readme()


class TestTheTwoAtRiskFiguresAreDistinguished:
    """The mislabelling, not just the number."""

    def test_the_corpus_figure_says_it_is_the_corpus(self) -> None:
        assert "Revenue at risk **in the corpus**" in _readme(), (
            "the corpus figure shares its label with a dashboard tile that "
            "measures a different population"
        )

    def test_it_explains_the_difference(self) -> None:
        text = _readme()
        assert "still **open**" in text
        assert "different populations" in text


class TestMoneyUsesIndianGrouping:
    """Lakh grouping, in prose as well as in code.

    `core/provenance.rupees` exists because "formatting an Indian merchant's
    revenue with Western grouping is a small thing that reads as not having
    thought about the market". The first fix for the stale figure wrote
    `₹861,995` — the very mistake the helper was written to prevent, one
    document over.
    """

    def test_no_western_grouped_lakh_figures(self) -> None:
        text = _readme()
        # Six or seven digits grouped as NNN,NNN is Western; Indian is N,NN,NNN.
        offenders = re.findall(r"\u20b9(\d{3},\d{3})(?![\d,])", text)
        assert not offenders, (
            f"Western digit grouping in the README: {offenders}. "
            "Indian grouping puts the first comma after three digits from the "
            "right and every two thereafter."
        )

    def test_the_helper_agrees(self) -> None:
        """Guards the premise: 8,61,995 is what `rupees` produces."""
        assert rupees(86199509).startswith("8,61,995")
