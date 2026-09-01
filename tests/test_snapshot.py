"""The evidence snapshot must not publish zeros.

`docs/EVIDENCE.md` exists so that no two documents disagree about a figure, and
it published **Tests collected: 0** on its first two runs. Twice, for two
different reasons:

1. `overview()` returned a placeholder `net_incremental` of zero that every
   caller was expected to overwrite (INC-039).
2. `pytest --collect-only -q` omits the "N tests collected" summary line the
   parser looks for, so the count fell through to its `except` and returned 0.

Both were silent. Neither raised. A figure that defaults to zero on failure is
worse than one that raises, because zero is a plausible number -- and in a file
whose entire purpose is being trusted, a plausible wrong number is the worst
possible failure mode.

So these tests assert that the snapshot's figures are *impossible* rather than
merely present: a repository with a thousand tests cannot have zero, and a
system with a measured lift cannot have a zero net incremental.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "EVIDENCE.md"


def _evidence() -> str:
    assert EVIDENCE.is_file(), (
        "docs/EVIDENCE.md is missing. Generate it with `python tasks.py snapshot`"
    )
    return EVIDENCE.read_text(encoding="utf-8")


def _row(label: str) -> str:
    """A cell from the snapshot's metadata table."""
    found = re.search(rf"\| {re.escape(label)} \| ([^|]+) \|", _evidence())
    assert found, f"no row labelled {label!r} in the snapshot"
    return found.group(1).strip()


class TestTheSnapshotIsIdentifiable:
    def test_it_carries_a_snapshot_id(self) -> None:
        """Without one, a figure that disagrees with another document is
        indistinguishable from carelessness."""
        assert re.match(r"`\d{8}-\d{4}-[0-9a-f]+`", _row("Snapshot"))

    def test_it_carries_a_commit(self) -> None:
        assert "unknown" not in _row("Commit")

    def test_it_carries_a_seed(self) -> None:
        assert "20260905" in _row("Corpus seed")


class TestNoFigureIsSilentlyZero:
    """The failure mode that actually happened, twice."""

    def test_the_test_count_is_real(self) -> None:
        """The committed snapshot must carry an actual count.

        Two failure modes, and this rejects both. A silent zero, which is what
        happened twice and is the worse one because zero is plausible. And
        "skipped (--fast)", which is honest but useless in the file a judge
        reads -- `--fast` exists for iteration, and this test is what stops a
        fast snapshot reaching a commit.
        """
        raw = _row("Tests collected").replace(",", "")
        assert "skipped" not in raw, (
            "this is a --fast snapshot. Regenerate with `python tasks.py "
            "snapshot` before committing -- the file judges read needs the "
            "real number."
        )
        assert raw.isdigit(), f"unparseable test count: {raw!r}"
        assert int(raw) > 500, (
            f"the snapshot reports {raw} tests. The collector fell through to "
            "its except branch and returned zero -- a plausible-looking wrong "
            "number in the one file that exists to be trusted."
        )

    def test_the_incident_count_is_not_zero(self) -> None:
        assert int(_row("Incidents")) > 30

    def test_the_decision_count_is_not_zero(self) -> None:
        assert int(_row("Decisions")) > 40

    def test_net_incremental_is_not_zero_when_there_is_a_lift(self) -> None:
        """INC-039. The snapshot published Rs 0.00 three lines above an
        attribution table reporting a 6.16% lift."""
        text = _evidence()
        # "percentage points", not "%", since DEC-047: a lift between two
        # proportions is a difference in points, and calling it a percentage
        # invites the reader to divide it by something. The guard clause below
        # caught this rename, which is what a guard clause is for.
        lift = re.search(r"Absolute lift \*\*([\d.]+) percentage points\*\*", text)
        net = re.search(r"\| Net incremental \| Rs ([\d,]+\.\d\d) \|", text)
        assert lift and net, "the snapshot's shape has changed"
        if float(lift.group(1)) > 0:
            assert float(net.group(1).replace(",", "")) > 0, (
                "a positive lift with a zero net incremental means the figure "
                "came from a placeholder, not from the attribution report"
            )


class TestTheCaveatsSurvive:
    @pytest.mark.parametrize(
        "phrase",
        [
            "Not proven",
            "pre-registered",
            "Contacts, breaches and escalations are measured",
            "A fresh clone shows Rs 0.00 here, and that is correct",
        ],
    )
    def test_the_honest_framing_is_present(self, phrase: str) -> None:
        assert phrase in _evidence(), f"the snapshot no longer says {phrase!r}"

    def test_the_benchmark_findings_are_included(self) -> None:
        text = _evidence()
        assert "costs nothing in" in text
        assert "UNAVAILABLE" in text

    def test_it_says_it_is_authoritative(self) -> None:
        """The line that makes it useful: a reader comparing two documents needs
        to know which one wins."""
        assert "is stale" in _evidence()
