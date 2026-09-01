"""Case ids the pitch script tells you to click on camera must be real.

An earlier draft of `docs/PITCH.md` instructed the presenter to click `RC-0142`
while narrating *"Ananya's ₹4,299 order failed, bank-side UPI timeout"*.
`RC-0142` is a ₹3,551 `INTENT_DECAY` abandoned checkout. The hero case is
`RC-0001`.

That would have been thirty seconds of a judge watching the screen disagree
with the voice-over, in the segment meant to establish that the numbers are
real. A wrong figure in a document is embarrassing; a wrong figure a judge can
see contradicted live is disqualifying.

Checked against the committed seed corpus, which is what a judge's clone will
contain.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PITCH = ROOT / "docs" / "PITCH.md"
SEED = ROOT / "data" / "revpilot.seed.db"


def _pitch() -> str:
    assert PITCH.is_file(), "docs/PITCH.md is missing; this test would pass vacuously"
    return PITCH.read_text(encoding="utf-8")


def _rendered_text() -> str:
    """The words a viewer sees, with JSX stripped.

    A raw substring search over `.tsx` cannot find "What we have not proven",
    because it is authored as ``What we have <span ...>not</span> proven``. The
    first version of this test reported that heading as missing from the UI --
    a false positive which, taken at face value, would have sent me to "fix" a
    panel that was already correct.

    Returns the raw source **and** a tag-stripped copy, concatenated, because
    each form misses what the other finds and this test only ever asks whether a
    string is present somewhere.

    Stripping alone is not enough: `{...}` removal also eats the object literal
    holding `{ label: "Held as control" }`, so the aggressive stripper produced a
    second false positive immediately after fixing the first. Two cheap views
    beat one clever one here.
    """
    components = ROOT / "apps" / "web" / "src" / "components"
    parts = []
    for path in sorted(components.glob("*.tsx")):
        raw = path.read_text(encoding="utf-8")
        parts.append(raw)
        # Tags removed so text split across elements reads as one string.
        parts.append(re.sub(r"\s+", " ", re.sub(r"<[^>]*>", " ", raw)))
    return "\n".join(parts)


@pytest.fixture(scope="module")
def attempts() -> list[tuple[str, int, str | None, str | None]]:
    """The corpus the cases are built from.

    Read from `payment_attempts` rather than `recovery_cases`: the seed ships
    with no cases, because the batch creates them. A test asserting on cases
    would fail on a clean clone for a reason that has nothing to do with the
    script.
    """
    c = sqlite3.connect(f"file:{SEED}?mode=ro", uri=True)
    try:
        return list(
            c.execute(
                "select id, amount_paise, error_reason, error_source "
                "from payment_attempts order by id"
            )
        )
    finally:
        c.close()


class TestTheHeroCaseIsRight:
    def test_the_script_names_rc_0001(self) -> None:
        assert "`RC-0001`" in _pitch(), (
            "the hero-case shot must name RC-0001, the Rs 4,299 bank-timeout case"
        )

    def test_the_script_no_longer_names_rc_0142_as_the_hero(self) -> None:
        """RC-0142 may still be *mentioned* -- the script explains the mistake --
        but not as the case to click."""
        text = _pitch()
        shot = next(
            (line for line in text.splitlines() if "Shot:" in line and "Cases table" in line),
            "",
        )
        assert "RC-0142" not in shot, f"the shot line still points at RC-0142: {shot}"

    def test_a_4299_bank_timeout_attempt_exists_in_the_corpus(
        self, attempts: list[tuple[str, int, str | None, str | None]]
    ) -> None:
        """The narration's facts, against the committed data.

        If the seed changes and this amount disappears, the voice-over becomes
        wrong and this fails -- which is the moment to rewrite it.
        """
        matches = [
            a
            for a in attempts
            if a[1] == 429_900
            and (a[2] or "") == "payment_failed_due_to_bank_timeout"
            and a[3] == "bank"
        ]
        assert matches, (
            "no Rs 4,299 bank-timeout attempt in the corpus; the hero-case "
            "narration in docs/PITCH.md describes one"
        )

    def test_the_narrated_amount_matches(self) -> None:
        """4,299 appears in the script and in the corpus. Pinned together."""
        assert "4,299" in _pitch()


class TestTheShotsPointAtThingsThatExist:
    @pytest.mark.parametrize(
        "phrase",
        [
            "Prove it against real Razorpay",
            "What we have not proven",
            "Held as control",
            "Where the AI stops",
        ],
    )
    def test_named_ui_elements_exist_in_the_components(self, phrase: str) -> None:
        """Every panel or button the script tells the presenter to click must be
        a string that actually appears in the UI."""
        assert phrase in _pitch(), f"the script no longer mentions {phrase!r}"
        assert phrase in _rendered_text(), (
            f"the script tells the presenter to click {phrase!r}, which no component renders"
        )

    def test_the_timings_are_monotonic(self) -> None:
        """A retimed script with an overlapping or reversed segment is a script
        that cannot be followed."""
        starts = [
            int(m[0]) * 60 + int(m[1])
            for m in re.findall(r"^### (\d):(\d\d) \u2013 \d:\d\d", _pitch(), re.M)
        ]
        assert len(starts) >= 6, f"only found {len(starts)} timed segments"
        assert starts == sorted(starts), f"segment start times are out of order: {starts}"

    def test_the_script_fits_five_minutes(self) -> None:
        ends = [
            int(m[0]) * 60 + int(m[1])
            for m in re.findall(r"^### \d:\d\d \u2013 (\d):(\d\d)", _pitch(), re.M)
        ]
        assert ends, "no segment end times found"
        assert max(ends) <= 5 * 60, f"the script runs to {max(ends)}s, over the 5-minute limit"
