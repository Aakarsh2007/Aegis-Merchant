"""Numbers the documents assert must match the repository.

I have corrected these by hand six times in this project, and got it wrong often
enough that a scan was worth writing: ``docs/PITCH.md`` once claimed 1,028 tests
when the suite had 1,092, and 29 incidents when there were 33.

Then a reviewer read the README against the demo script and found **three**
disagreements at once -- 1,199 vs 1,149 tests, 39 vs 40 incidents, 45 vs 46
decisions -- while this file was green. Two separate reasons, both worth
recording, because the fix for each is structural:

1. **The scan did not cover the file that drifted.** ``DOCS`` listed README,
   PITCH, workflow, DEPLOYMENT and PRE-REGISTRATION. All three stale figures
   were in ``docs/DEMO-SCRIPT.md``, which was written after this test and never
   added to the list. A consistency check whose scope excludes a document is
   worse than no check, because it reports safety over ground it never looked at.
   ``DOCS`` is now *derived* -- every markdown file in ``docs/`` plus the two at
   the root -- so a new document is covered the moment it exists.

2. **The regexes matched one phrasing each.** ``(\\d+) documented incidents``
   never matched "39 incidents in docs/INCIDENTS.md", or the spelled-out
   "Thirty-nine incidents" the spoken script uses, or a figures-table row
   reading ``| 40 * 46 |``. All three forms were in use.

3. **The test count was a one-sided bound, not a value.** It asserted only that
   no document claimed *more* tests than a crude estimate supported, with a 1.6x
   ceiling. 1,149 against a real 1,199 sailed through. The count is now compared
   against ``docs/EVIDENCE.md``, which gets it from ``pytest --collect-only``.

The counts are read from the repository, so this file needs no editing when the
real numbers change. It fails only when a *document* falls behind.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.tools.docmeta import (
    DECISION_HEADING,
    INCIDENT_HEADING,
    count_headings,
    decision_count,
    incident_count,
)

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "docs" / "EVIDENCE.md"

#: Documents that record the past on purpose, and are therefore *supposed* to
#: quote figures that are no longer current.
#:
#: Excluding anything is uncomfortable here -- "the check did not cover the file
#: that drifted" is the bug this rewrite exists to fix, and an exclusion list is
#: how that bug comes back. So the rule is narrow and stated: these three are
#: append-only or frozen, and their stale numbers are *dated facts*, not claims.
#:
#: * ``INCIDENTS.md`` / ``DECISIONS.md`` -- entries are written at a moment and
#:   never revised. INC-006 says the suite had 1,040 tests when it was written.
#:   That was true. Rewriting it to 1,199 would falsify the record.
#: * ``workflow.md`` -- the original build spec, frozen before implementation.
#:
#: ``test_the_exclusions_are_exactly_these`` pins the list so it cannot quietly
#: grow to cover up a real drift.
HISTORICAL: frozenset[str] = frozenset({"INCIDENTS.md", "DECISIONS.md", "workflow.md"})

#: Every prose document, derived rather than listed. See reason (1) above.
ALL_DOCS: list[Path] = sorted(
    [*(ROOT / "docs").glob("*.md"), ROOT / "README.md", ROOT / "workflow.md"]
)

#: The ones making present-tense claims to a reader.
DOCS: list[Path] = [d for d in ALL_DOCS if d.name not in HISTORICAL]

#: Numbers this project spells out because they are read aloud.
WORDS: dict[str, int] = {
    # Twenty-somethings included because `docs/PITCH.md` carried "Twenty-nine
    # incidents" long after there were forty-six, and the first version of this
    # table started at thirty-five -- so the scan could not see it. A lookup
    # table that only covers the range you expect is the same defect as a DOCS
    # list that omits the file that drifted.
    "twenty-five": 25,
    "twenty-six": 26,
    "twenty-seven": 27,
    "twenty-eight": 28,
    "twenty-nine": 29,
    "thirty": 30,
    "thirty-one": 31,
    "thirty-two": 32,
    "thirty-three": 33,
    "thirty-four": 34,
    "thirty-five": 35,
    "thirty-six": 36,
    "thirty-seven": 37,
    "thirty-eight": 38,
    "thirty-nine": 39,
    "forty": 40,
    "forty-one": 41,
    "forty-two": 42,
    "forty-three": 43,
    "forty-four": 44,
    "forty-five": 45,
    "forty-six": 46,
    "forty-seven": 47,
    "forty-eight": 48,
}


def _incident_count() -> int:
    return incident_count(ROOT)


def _decision_count() -> int:
    return decision_count(ROOT)


def _claims(text: str, noun: str) -> set[int]:
    """Every count of ``noun`` a document asserts, in any phrasing we use.

    Numerals, spelled-out words, and the ``| 39 * 45 |`` figures-table row. The
    narrow version of this missed all three of the forms that had actually
    drifted, so it is now deliberately generous: a false positive here costs one
    edit, and a false negative cost a reviewer finding.
    """
    found: set[int] = set()
    plural = f"{noun}s"
    # "39 incidents", "39 documented incidents", "39 written incidents"
    found |= {int(n) for n in re.findall(rf"(\d+)\s+(?:\w+\s+){{0,2}}?{plural}\b", text, re.I)}
    # "Thirty-nine incidents"
    for word, value in WORDS.items():
        if re.search(rf"\b{word}\s+(?:\w+\s+){{0,2}}?{plural}\b", text, re.I):
            found.add(value)
    return found


def _table_pairs(text: str) -> set[tuple[int, int]]:
    """Rows of the form ``| Incidents * decisions | 39 * 45 |``.

    The demo script's figures table carried ``40 * 46`` here, in a row whose
    label named both documents. No regex above would find it, because the
    numbers are not adjacent to the nouns.
    """
    pairs: set[tuple[int, int]] = set()
    for line in text.splitlines():
        if not line.startswith("|"):
            continue
        if "incident" not in line.lower() or "decision" not in line.lower():
            continue
        numbers = re.findall(r"\b(\d{2,4})\b", line)
        if len(numbers) >= 2:
            pairs.add((int(numbers[0]), int(numbers[1])))
    return pairs


def _evidence_figure(label: str) -> int | None:
    """A row of the snapshot's metadata table, e.g. ``| Tests collected | 1,199 |``."""
    if not EVIDENCE.is_file():
        return None
    found = re.search(
        rf"^\|\s*{label}\s*\|\s*([\d,]+)\s*\|", EVIDENCE.read_text(encoding="utf-8"), re.M | re.I
    )
    return int(found.group(1).replace(",", "")) if found else None


class TestTheCountsAreReadable:
    """Guards the scan. A refactor that broke these would otherwise make every
    assertion below pass on zero."""

    def test_incidents_are_countable(self) -> None:
        assert _incident_count() >= 30

    def test_decisions_are_countable(self) -> None:
        assert _decision_count() >= 40

    def test_the_documents_were_found(self) -> None:
        names = {d.name for d in DOCS}
        assert {"README.md", "DEMO-SCRIPT.md", "PITCH.md", "EVIDENCE.md"} <= names, (
            f"the derived DOCS list is missing something: {sorted(names)}"
        )

    def test_the_exclusions_are_exactly_these(self) -> None:
        """An exclusion list is how "the check did not cover that file" comes
        back. Pinned, so adding to it is a visible decision."""
        assert {d.name for d in ALL_DOCS} - {d.name for d in DOCS} == HISTORICAL

    def test_the_scan_covers_the_documents_a_judge_reads(self) -> None:
        names = {d.name for d in DOCS}
        assert "DEMO-SCRIPT.md" in names, (
            "the demo script is the document a presenter reads on camera; it is "
            "where all three stale figures were found, and it must be scanned"
        )

    @pytest.mark.parametrize(
        ("filename", "loose", "strict"),
        [
            ("INCIDENTS.md", r"^## INC-", INCIDENT_HEADING),
            ("DECISIONS.md", r"^## DEC-", DECISION_HEADING),
        ],
    )
    def test_the_heading_pattern_excludes_the_format_templates(
        self, filename: str, loose: str, strict: str
    ) -> None:
        """The bug that produced 40 and 46.

        Both documents open with a template heading using ``00N``. The loose
        pattern matches it; the strict one must not.

        The first version of this asserted the two counts differed by *exactly
        one*, and it broke immediately -- INC-041, the entry about this very bug,
        quotes the template inside a fenced code block, so the loose pattern
        found two. That assertion was pinning an incidental number instead of
        the behaviour, which is a smaller instance of the mistake the bug was.
        """
        path = ROOT / "docs" / filename
        assert count_headings(loose, path) > count_headings(strict, path), (
            f"{filename} no longer contains anything for the loose pattern to "
            "over-match, so this test proves nothing"
        )
        assert count_headings(r"^## (?:INC|DEC)-00N ", path) >= 1, (
            "the format template heading has moved or changed shape; if it is "
            "gone for good this test should be deleted rather than relaxed"
        )
        assert count_headings(strict, path) == count_headings(r"^## (?:INC|DEC)-\d{3} ", path), (
            "the strict pattern is matching something other than real entries"
        )

    def test_the_evidence_snapshot_carries_the_figures(self) -> None:
        for label in ("Tests collected", "Incidents", "Decisions"):
            assert _evidence_figure(label) is not None, (
                f"docs/EVIDENCE.md has no '{label}' row; the assertions that "
                "compare documents against it would pass vacuously"
            )


class TestTheSnapshotIsRight:
    """``EVIDENCE.md`` is the single source of truth, so it is the one file whose
    own numbers have to be checked against the repository rather than against it."""

    def test_the_snapshot_test_count_is_exact(self, request: pytest.FixtureRequest) -> None:
        """The snapshot's own test count, against the suite that is running.

        This gap was found by cloning the repository fresh and collecting: the
        clean clone reported 1,260 while `EVIDENCE.md` said 1,257, because the
        snapshot was regenerated three test-edits before the work finished.

        Nothing caught it. `test_test_count_claims_match_the_snapshot` compares
        every *document* against the snapshot, and they all agreed -- with each
        other, and with a stale figure. I had made the incident and decision
        counts verifiable against the repository and left the test count with no
        equivalent, because the obvious way to check it is to run pytest inside
        pytest, which is slow and fragile.

        It turns out not to be needed: pytest already knows. ``session.testscollected``
        is the exact number, free, from the run in progress.

        Skipped when the invocation is filtered, because then the number is the
        subset's and comparing it to the full suite's would fail every time
        anyone ran a single file.
        """
        session = request.session
        if session.config.option.keyword or session.config.option.markexpr:
            pytest.skip("filtered invocation: -k/-m makes the collected count a subset")
        # `file_or_dir` is empty for a bare `pytest` run, which takes its paths
        # from `testpaths` in the config. Anything explicit means a subset.
        targets = [t for t in session.config.option.file_or_dir if t not in {".", "tests"}]
        if targets:
            pytest.skip(f"subset invocation ({targets}): not the whole suite")

        collected = session.testscollected
        assert collected > 500, (
            f"only {collected} tests collected; this looks like a subset that "
            "slipped past the guards above, and asserting on it would be noise"
        )
        assert _evidence_figure("Tests collected") == collected, (
            f"docs/EVIDENCE.md says {_evidence_figure('Tests collected')} tests; "
            f"this run collected {collected}. Regenerate with "
            "`python tasks.py snapshot` -- and note that every document is "
            "checked against the snapshot, so a stale snapshot makes all of them "
            "agree on the wrong number."
        )

    def test_the_snapshot_incident_count_is_exact(self) -> None:
        assert _evidence_figure("Incidents") == _incident_count(), (
            "docs/EVIDENCE.md disagrees with docs/INCIDENTS.md. Regenerate with "
            "`python tasks.py snapshot`. This was wrong by exactly one for a "
            "while, because the counter matched the file's own format template."
        )

    def test_the_snapshot_decision_count_is_exact(self) -> None:
        assert _evidence_figure("Decisions") == _decision_count(), (
            "docs/EVIDENCE.md disagrees with docs/DECISIONS.md. Regenerate with "
            "`python tasks.py snapshot`."
        )


class TestNoDocumentContradictsTheRepository:
    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_incident_claims_are_current(self, doc: Path) -> None:
        actual = _incident_count()
        claims = _claims(doc.read_text(encoding="utf-8"), "incident")
        stale = {n for n in claims if n != actual}
        assert not stale, (
            f"{doc.name} claims {sorted(stale)} incidents; docs/INCIDENTS.md has "
            f"{actual}. A judge who checks one number and finds it stale has a "
            "reason to check the others."
        )

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_decision_claims_are_current(self, doc: Path) -> None:
        actual = _decision_count()
        claims = _claims(doc.read_text(encoding="utf-8"), "decision")
        stale = {n for n in claims if n != actual}
        assert not stale, (
            f"{doc.name} claims {sorted(stale)} decisions; docs/DECISIONS.md has {actual}"
        )

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_figures_table_pairs_are_current(self, doc: Path) -> None:
        """The ``| Incidents * decisions | 39 * 45 |`` row form."""
        expected = (_incident_count(), _decision_count())
        for pair in _table_pairs(doc.read_text(encoding="utf-8")):
            assert pair == expected, (
                f"{doc.name} has a table row reading {pair[0]} incidents and "
                f"{pair[1]} decisions; the repository has {expected[0]} and "
                f"{expected[1]}"
            )

    @pytest.mark.parametrize("doc", [d for d in DOCS if d.is_file()], ids=lambda d: d.name)
    def test_test_count_claims_match_the_snapshot(self, doc: Path) -> None:
        """Exact, against a figure obtained by actually collecting the suite.

        This replaced a one-sided bound with a 1.6x ceiling, which let a stale
        1,149 pass against a real 1,199. The bound existed because the old
        estimator counted ``def test_`` by hand and undercounted badly; the
        snapshot runs ``pytest --collect-only``, so an exact comparison is now
        available and there is no reason to accept less.
        """
        expected = _evidence_figure("Tests collected")
        assert expected, "no test count in docs/EVIDENCE.md"
        text = doc.read_text(encoding="utf-8")
        claims = {int(n.replace(",", "")) for n in re.findall(r"([\d,]{3,})\s+tests\b", text)}
        # A round-number aspiration like "1,000 tests" in prose about goals is
        # not a claim about this suite; only figures within a plausible band are.
        stale = {n for n in claims if n != expected and expected * 0.5 <= n <= expected * 2}
        assert not stale, (
            f"{doc.name} claims {sorted(stale)} tests; docs/EVIDENCE.md reports "
            f"{expected}, collected from the suite. Regenerate the snapshot and "
            "update the document."
        )


class TestTheHeadlineClaimsAgree:
    def test_readme_and_pitch_quote_the_same_incident_count(self) -> None:
        r = _claims((ROOT / "README.md").read_text(encoding="utf-8"), "incident")
        p = _claims((ROOT / "docs" / "PITCH.md").read_text(encoding="utf-8"), "incident")
        if r and p:
            assert r == p, f"README says {sorted(r)}, PITCH says {sorted(p)}"

    def test_the_pre_registration_is_never_edited_after_registration(self) -> None:
        """It says so about itself. Worth pinning, because the credibility of the
        whole pre-registration rests on that promise."""
        text = (ROOT / "docs" / "PRE-REGISTRATION.md").read_text(encoding="utf-8")
        assert "Unamended" in text, (
            "the pre-registration no longer says whether it has been amended; "
            "if it HAS been, that belongs in a new dated section with the "
            "original text left in place"
        )
