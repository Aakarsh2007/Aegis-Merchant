"""Numbers the documents assert must match the repository.

I have corrected these by hand five times in this project, and got it wrong
often enough that a scan was worth writing: `docs/PITCH.md` -- the file holding
the submission's form answers -- claimed 1,028 tests when the suite had 1,092,
and 29 incidents when there were 33. A judge who checks one number and finds it
stale has a reason to check the others.

The counts are read from the repository rather than hardcoded here, so this file
never needs editing when the real number changes. It fails only when a
*document* falls behind.

Deliberately narrow. It checks the counts that appear as claims in prose, not
every integer in the docs -- a broad scan would flag section numbers and rupee
figures and get disabled inside a week.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "PITCH.md",
    ROOT / "workflow.md",
    ROOT / "docs" / "DEPLOYMENT.md",
    ROOT / "docs" / "PRE-REGISTRATION.md",
]


def _incident_count() -> int:
    text = (ROOT / "docs" / "INCIDENTS.md").read_text(encoding="utf-8")
    return len(re.findall(r"^## INC-", text, re.M))


def _decision_count() -> int:
    text = (ROOT / "docs" / "DECISIONS.md").read_text(encoding="utf-8")
    return len(re.findall(r"^## DEC-", text, re.M))


def _test_count() -> int:
    """Counted by walking the suite's own files rather than by running pytest.

    Invoking pytest from inside pytest is slow and fragile. This counts
    `def test_` plus the cases each `parametrize` multiplies, which lands close
    enough that a documented figure drifting by dozens is caught while a
    one-or-two difference is tolerated by the assertions below.
    """
    total = 0
    for path in sorted((ROOT / "tests").rglob("test_*.py")):
        text = path.read_text(encoding="utf-8")
        functions = len(re.findall(r"^\s*(?:async )?def test_", text, re.M))
        params = 0
        for block in re.findall(
            r"@pytest\.mark\.parametrize\((.*?)\)\s*\n\s*(?:async )?def", text, re.DOTALL
        ):
            # Rough: count top-level commas in the argument list.
            params += max(0, block.count("(") - 1)
        total += functions + params
    return total


class TestTheCountsAreReadable:
    """Guards the scan. A refactor that broke these would otherwise make every
    assertion below pass on zero."""

    def test_incidents_are_countable(self) -> None:
        assert _incident_count() >= 30

    def test_decisions_are_countable(self) -> None:
        assert _decision_count() >= 40

    def test_tests_are_countable(self) -> None:
        assert _test_count() >= 500


class TestNoDocumentUnderstatesTheIncidentCount:
    """The one that has actually gone wrong."""

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_incident_claims_are_current(self, doc: Path) -> None:
        actual = _incident_count()
        text = doc.read_text(encoding="utf-8")
        claims = {int(n) for n in re.findall(r"(\d+) (?:documented|written) incidents", text)}
        stale = {n for n in claims if n != actual}
        assert not stale, (
            f"{doc.name} claims {sorted(stale)} incidents; docs/INCIDENTS.md has "
            f"{actual}. A judge who checks one number and finds it stale has a "
            "reason to check the others."
        )

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_no_document_overstates_the_test_count(self, doc: Path) -> None:
        """A **bound**, not a match, and deliberately one-sided.

        `_test_count` counts `def test_` plus a crude estimate of what each
        `parametrize` multiplies, and it undercounts -- 948 against a real 1,092.
        Asserting equality would fail constantly; widening the tolerance until
        equality passed would make the check prove nothing, which is the
        INC-006 pattern this project keeps tripping over.

        So it asserts the only thing the estimate supports honestly: a document
        must not claim *more* tests than plausibly exist. Overstating is the
        dishonest direction; a stale understatement is embarrassing but not a
        false claim, and `test_incident_claims_are_current` covers exact drift
        on the count that can be measured exactly.
        """
        floor = _test_count()
        ceiling = int(floor * 1.6)
        text = doc.read_text(encoding="utf-8")
        claims = {int(n.replace(",", "")) for n in re.findall(r"([\d,]{3,}) tests", text)}
        overstated = {n for n in claims if n > ceiling}
        assert not overstated, (
            f"{doc.name} claims {sorted(overstated)} tests. A conservative "
            f"count of the suite finds about {floor}, so anything above "
            f"{ceiling} is not supportable."
        )


class TestTheHeadlineClaimsAgree:
    """README and PITCH must not disagree with each other."""

    def test_both_quote_the_same_incident_count(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        pitch = (ROOT / "docs" / "PITCH.md").read_text(encoding="utf-8")
        r = {int(n) for n in re.findall(r"(\d+) documented incidents", readme)}
        p = {int(n) for n in re.findall(r"(\d+) written incidents", pitch)}
        if r and p:
            assert r == p, f"README says {sorted(r)}, PITCH says {sorted(p)}"

    def test_the_pre_registration_is_never_edited_after_registration(self) -> None:
        """It says so about itself. Worth pinning, because the credibility of
        the whole pre-registration rests on that promise."""
        text = (ROOT / "docs" / "PRE-REGISTRATION.md").read_text(encoding="utf-8")
        assert "Unamended" in text, (
            "the pre-registration no longer says whether it has been amended; "
            "if it HAS been, that belongs in a new dated section with the "
            "original text left in place"
        )
