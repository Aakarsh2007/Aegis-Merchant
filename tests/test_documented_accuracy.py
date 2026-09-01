"""No document may quote a gate accuracy that the scorer does not produce.

The Phase 6 gate -- the rule table beating the model, measured before the
routing decision was made -- is the whole of this project's claim to have used
AI where it helped and removed it where it did not. It is quoted in the README,
the pitch, the demo script and the evidence snapshot.

All four said **96.5% against 90.6% on an 85-case golden set**. The scorer says
**96.4% against 90.4% over 83 cases**. Both decimals were rounded up by a tenth
and the denominator was wrong, and the version in ``docs/EVIDENCE.md`` was a
hardcoded string literal inside the generator of the file whose whole claim is
"generated, not written". See INC-045.

Nobody had flagged it. The reviewer passes that found the money and the counts
went past this figure four times, because a number that looks plausible and
appears identically in four places reads as corroborated.

So: the scorer is the authority, and this compares every document against it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.llm.gate import gate_scores

ROOT = Path(__file__).resolve().parents[1]

#: Documents making present-tense claims. `INCIDENTS.md` and `DECISIONS.md` are
#: append-only records whose stale figures are dated facts -- INC-045 quotes
#: 96.5% as the wrong number, which is the point of the entry.
DOCS: list[Path] = [
    ROOT / "README.md",
    ROOT / "docs" / "PITCH.md",
    ROOT / "docs" / "DEMO-SCRIPT.md",
    ROOT / "docs" / "EVIDENCE.md",
]

#: Any percentage in the 80-100 band, which is where an accuracy claim lives.
#: Deliberately wide: the failure was two figures a tenth of a point off, so a
#: pattern that only looked for the exact stale values would have found nothing.
_PCT = re.compile(r"\b(\d{2}\.\d)%")


@pytest.fixture(scope="module")
def gate() -> object:
    return gate_scores()


class TestTheScorerIsUsable:
    """Guards everything below. If the cache or golden set is missing, the
    comparisons would pass against zero."""

    def test_the_golden_set_is_loaded(self, gate: object) -> None:
        assert gate.golden_cases >= 80  # type: ignore[attr-defined]

    def test_the_cache_covers_most_of_it(self, gate: object) -> None:
        assert gate.scored_cases >= gate.golden_cases * 0.9  # type: ignore[attr-defined]

    def test_the_two_counts_are_not_the_same_number(self, gate: object) -> None:
        """The specific confusion. The docs said "85-case golden set" while the
        accuracy was computed over 83.

        If the cache ever covers everything these become equal and this test
        should be deleted rather than relaxed -- but while they differ, the
        difference is exactly what got misreported.
        """
        g = gate  # type: ignore[assignment]
        assert g.scored_cases <= g.golden_cases  # type: ignore[attr-defined]

    def test_the_rule_table_still_wins(self, gate: object) -> None:
        """DEC-017's premise. If this flips, the routing decision needs
        revisiting and every document needs rewriting -- not this test
        loosening."""
        assert gate.rule_table_wins  # type: ignore[attr-defined]


class TestNoDocumentMisquotesTheGate:
    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_accuracy_figures_are_the_measured_ones(self, doc: Path, gate: object) -> None:
        """Any 2-decimal percentage near an accuracy claim must be one we produce.

        Scoped to lines that mention the rule table, the model or the golden
        set, so unrelated percentages -- conversion rates, cache hit rates,
        confidence -- are not swept in. That scoping is the part to be careful
        with: too tight and this becomes the check that looked at the wrong
        file, which is INC-041.
        """
        allowed = {
            round(gate.baseline.accuracy * 100, 1),  # type: ignore[attr-defined]
            round(gate.model.accuracy * 100, 1),  # type: ignore[attr-defined]
        }
        offenders: list[tuple[int, str, float]] = []
        for n, line in enumerate(doc.read_text(encoding="utf-8").splitlines(), 1):
            low = line.lower()
            if not any(k in low for k in ("rule table", "golden", "model accuracy", "the model")):
                continue
            for found in _PCT.findall(line):
                value = float(found)
                if 80.0 <= value <= 100.0 and value not in allowed:
                    offenders.append((n, line.strip()[:90], value))
        assert not offenders, (
            f"{doc.name} quotes accuracy figures the scorer does not produce "
            f"(it produces {sorted(allowed)}): {offenders}. Regenerate with "
            "`python tasks.py snapshot` and update the prose."
        )

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_no_document_claims_the_accuracy_was_over_the_full_golden_set(
        self, doc: Path, gate: object
    ) -> None:
        """The denominator, which is the half nobody noticed.

        "96.5% on an 85-case golden set" was wrong twice: the rate, and the
        claim that it was computed over all 85. Only 83 are scoreable.
        """
        if gate.scored_cases == gate.golden_cases:  # type: ignore[attr-defined]
            pytest.skip("the cache now covers the whole golden set; nothing to confuse")
        full = gate.golden_cases  # type: ignore[attr-defined]
        text = doc.read_text(encoding="utf-8")
        bad = re.findall(rf"{full}[- ]case golden set", text, re.I)
        assert not bad, (
            f"{doc.name} says the accuracy is over a {full}-case golden set. It is "
            f"computed over {gate.scored_cases} -- the cases the committed cache "  # type: ignore[attr-defined]
            "covers. An uncached case has no model answer to score."
        )


class TestTheSnapshotComputesItRatherThanQuotingIt:
    def test_the_generator_holds_no_hardcoded_accuracy(self) -> None:
        """The actual root cause.

        ``tools/snapshot.py`` carried the two figures as string literals. A
        generated file containing hand-typed numbers is not a generated file,
        and it is worse than an ordinary stale document because it launders the
        mistake as a measurement.

        Only the f-string interpolations and the comment recording the incident
        may mention them now.
        """
        source = (ROOT / "apps" / "api" / "app" / "tools" / "snapshot.py").read_text(
            encoding="utf-8"
        )
        for line in source.splitlines():
            if line.lstrip().startswith("#"):
                continue  # the INC-045 note quotes the wrong figures deliberately
            assert not re.search(r'"[^"]*9[0-9]\.[0-9]%[^"]*"', line), (
                f"snapshot.py has a hardcoded accuracy percentage: {line.strip()}"
            )
