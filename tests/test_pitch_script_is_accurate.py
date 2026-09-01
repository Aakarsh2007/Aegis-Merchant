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
SCRIPT = ROOT / "docs" / "DEMO-SCRIPT.md"
SEED = ROOT / "data" / "revpilot.seed.db"


def _pitch() -> str:
    assert PITCH.is_file(), "docs/PITCH.md is missing; this test would pass vacuously"
    return PITCH.read_text(encoding="utf-8")


def _script() -> str:
    """The shooting script.

    Separate from `_pitch()` on purpose: `PITCH.md` holds the form answers and
    points here, because two documents carrying two versions of one script is
    the drift the evidence snapshot exists to prevent, one level up.
    """
    assert SCRIPT.is_file(), "docs/DEMO-SCRIPT.md is missing; this would pass vacuously"
    return SCRIPT.read_text(encoding="utf-8")


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
        assert "`RC-0001`" in _script(), (
            "the hero-case shot must name RC-0001, the Rs 4,299 bank-timeout case"
        )

    def test_the_script_no_longer_names_rc_0142_as_the_hero(self) -> None:
        """RC-0142 may still be *mentioned* -- the script explains the mistake --
        but not as the case to click."""
        text = _script()
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
        """The amount is stated, in numerals or in words.

        The narration spells it out — "four thousand two hundred and ninety-nine
        rupees" — because it is read aloud, and an earlier version of this test
        demanded the numeral `4,299`. It failed against a correct script, which
        is the same false-positive shape as the JSX matcher above.
        """
        script = _script()
        assert "4,299" in script or "ninety-nine rupees" in script, (
            "the hero case's amount is not stated in the narration"
        )


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
        assert phrase in _script(), f"the script no longer mentions {phrase!r}"
        assert phrase in _rendered_text(), (
            f"the script tells the presenter to click {phrase!r}, which no component renders"
        )

    def test_the_timings_are_monotonic(self) -> None:
        """A retimed script with an overlapping or reversed segment is a script
        that cannot be followed."""
        starts = [
            int(m[0]) * 60 + int(m[1]) for m in re.findall(r"^### `(\d):(\d\d) –", _script(), re.M)
        ]
        assert len(starts) >= 6, f"only found {len(starts)} timed segments"
        assert starts == sorted(starts), f"segment start times are out of order: {starts}"

    def test_the_script_fits_five_minutes(self) -> None:
        ends = [int(m[0]) * 60 + int(m[1]) for m in re.findall(r"– (\d):(\d\d)`", _script(), re.M)]
        assert ends, "no segment end times found"
        assert max(ends) <= 5 * 60, f"the script runs to {max(ends)}s, over the 5-minute limit"


# ===========================================================================
class TestTheScriptCanActuallyBeSpoken:
    """A script nobody can read at speaking pace is not a script.

    INC-040. The first version of `docs/DEMO-SCRIPT.md` carried 1,586 words of
    narration across five minutes — **317 words per minute.** Normal clear
    speech is 130–150; a fast presenter reaches 180. The document asserted "about
    700 words, which fits five minutes with room to breathe" and I had never
    counted it.

    The failure would have surfaced on camera, mid-take, with the deadline four
    days away. A reviewer flagged the density; measuring it was the fix.

    So the pacing is now a test. Words are cheap to add and the arithmetic is
    unforgiving.
    """

    #: Comfortable, clear delivery. Above this a presenter is rushing, and a
    #: rushed presenter reads instead of explaining.
    MAX_WPM = 170
    #: Below this the segment is under-filled and the timing is wrong, not the
    #: speech. Worth catching too: dead air reads as unpreparedness.
    MIN_WPM = 85

    @staticmethod
    def _segments() -> list[tuple[str, int, int]]:
        """(timecode, spoken words, duration in seconds) for each segment."""
        text = _script()
        parts = re.split(r"^### `(\d:\d\d \u2013 \d:\d\d)` \u2014 (.+)$", text, flags=re.M)
        out: list[tuple[str, int, int]] = []
        for i in range(1, len(parts), 3):
            timecode, body = parts[i], parts[i + 2]
            say = re.search(r"\*\*SAY\*\*\n(.*?)(?=\n---|\Z)", body, re.DOTALL)
            if not say:
                continue
            # Spoken lines only: `> text`. Stage directions (`> **...**`) are
            # read by the presenter, not aloud.
            lines = [
                line[2:]
                for line in say.group(1).splitlines()
                if line.startswith("> ") and not line.startswith("> **")
            ]
            words = len(re.sub(r"[*_()]", "", " ".join(lines)).split())

            def seconds(stamp: str) -> int:
                minutes, secs = stamp.split(":")
                return int(minutes) * 60 + int(secs)

            start, end = timecode.split(" \u2013 ")
            out.append((timecode, words, seconds(end) - seconds(start)))
        return out

    def test_the_segments_are_parseable(self) -> None:
        """Guards every assertion below. A renamed heading would otherwise make
        the pacing checks pass on an empty list."""
        assert len(self._segments()) >= 8

    def test_the_whole_script_fits_five_minutes_of_speech(self) -> None:
        segments = self._segments()
        words = sum(w for _, w, _ in segments)
        wpm = words / 300 * 60
        assert wpm <= self.MAX_WPM, (
            f"the narration is {words} words over five minutes = {wpm:.0f} wpm. "
            "Clear speech is 130-150. This cannot be read aloud in the time."
        )

    def test_no_single_segment_is_unspeakable(self) -> None:
        offenders = [
            (tc, round(w / d * 60))
            for tc, w, d in self._segments()
            if d and w / d * 60 > self.MAX_WPM
        ]
        assert not offenders, (
            f"these segments cannot be spoken in their allotted time: {offenders}. "
            "Either cut words or give the segment more seconds."
        )

    def test_no_segment_is_mostly_silence(self) -> None:
        """The other direction. A segment far under pace usually means the
        timings were retimed and the words were not."""
        thin = [
            (tc, round(w / d * 60))
            for tc, w, d in self._segments()
            if d >= 15 and w / d * 60 < self.MIN_WPM
        ]
        assert not thin, f"these segments are under-filled: {thin}"

    def test_the_document_does_not_misstate_its_own_length(self) -> None:
        """The original claimed "about 700 words" while carrying 1,586.

        A document that misreports its own measurable property is the same defect
        class as a dashboard tile that misreports a figure.
        """
        actual = sum(w for _, w, _ in self._segments())
        # Only a TOTAL claim, not a rate. The first version matched "about 135
        # words a minute" and compared a words-per-minute figure against a total
        # word count -- a false positive on correct prose, which is the same
        # defect as the JSX matcher and the substring search before it. Three
        # times now that a checking tool has been wrong in the same direction.
        claimed = {int(n) for n in re.findall(r"(\d{3,4}) words across", _script())} | {
            int(n)
            for n in re.findall(r"narration is about (\d{3,4}) words(?! a minute)", _script())
        }
        assert claimed, "the script no longer states its own length"
        for claim in claimed:
            # 1,586 is quoted deliberately, as the figure the first draft had.
            if claim > actual * 2:
                continue
            assert abs(claim - actual) <= 60, f"the script says it is {claim} words; it is {actual}"
