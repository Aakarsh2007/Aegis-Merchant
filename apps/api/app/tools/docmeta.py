"""Counting the incidents and decisions, once, so two counters cannot disagree.

This module exists because of a one-character bug that produced three separate
"inconsistent number" findings in a reviewer's pass.

``tools/snapshot.py`` counted incidents with ``^## INC-`` and decisions with
``^## DEC-``. Both files open with a **format template** showing contributors
what a heading looks like::

    ## INC-00N - YYYY-MM-DD HH:MM IST - One-line symptom
    ## DEC-00N - YYYY-MM-DD - Title

Those matched. So ``docs/EVIDENCE.md`` -- the file whose entire purpose is being
the single source of truth -- published **40 incidents and 46 decisions** when
there were 39 and 45. The README, maintained by hand, was right. The generated
file was wrong, which is the worst possible direction for that error, and the
demo script had been "corrected" *toward* the generated figure.

There is a second, unrelated trap in the same data and it is worth recording
because it is why these functions return a count rather than a maximum id:
``INC-014`` and ``DEC-043`` do not exist. The ids run to 040 and 046 with one gap
each. Anything deriving a count from the highest id would be off by one in the
other direction and look plausible doing it.

The pattern therefore requires a three-digit id followed by a space, which the
templates' ``00N`` cannot satisfy. Asserted in
``tests/test_documented_counts.py`` against both the real entries and the
templates, so a future template that happens to match is caught.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

__all__ = [
    "DECISION_HEADING",
    "INCIDENT_HEADING",
    "count_headings",
    "decision_count",
    "incident_count",
]

#: A real entry: exactly three digits, then a space. The format templates use
#: ``00N``, so they cannot match, and neither can a prose mention of "INC-032"
#: mid-sentence, because that is not at the start of a line after "## ".
INCIDENT_HEADING: Final = r"^## INC-\d{3} "
DECISION_HEADING: Final = r"^## DEC-\d{3} "


def count_headings(pattern: str, path: Path) -> int:
    """Occurrences of ``pattern`` at line starts in ``path``.

    Returns 0 for a missing file rather than raising: the snapshot must still
    generate on a partial checkout, and a zero in the output is visible whereas
    a traceback halfway through writing ``EVIDENCE.md`` is not.
    """
    if not path.is_file():
        return 0
    return len(re.findall(pattern, path.read_text(encoding="utf-8"), re.M))


def incident_count(root: Path) -> int:
    return count_headings(INCIDENT_HEADING, root / "docs" / "INCIDENTS.md")


def decision_count(root: Path) -> int:
    return count_headings(DECISION_HEADING, root / "docs" / "DECISIONS.md")
