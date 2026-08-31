"""The frontend's rule descriptions must cover the rules the API sends.

INC-028. ``StoppingRulesPanel.tsx`` keyed its description map on the Python
enum's *member* names — ``S01_ALREADY_RESOLVED`` — which never cross the wire.
The API sends the enum's *value*, ``"S-01"``. So every one of the twelve rows
rendered with no description at all, in the panel whose entire purpose is to
name the brakes, and the defect was visible only to someone looking at the
screen. Two of the keys were wrong on their own terms as well
(``S03_DISCOUNT_BUDGET`` for ``S03_DISCOUNT_ATTEMPT_BUDGET``,
``S10_PROMISE_TO_PAY`` for ``S10_PROMISE_FREEZE``), which is what a map written
from memory rather than from the enum looks like.

Reading a ``.tsx`` file from a Python test is unusual, and it is the cheapest
instrument that can actually fail here. The alternative — moving the labels into
the API — would couple presentation to the backend for no gain; the component's
own docstring argues against it. This keeps the labels in the client and makes
the coupling that matters, *the key set*, checked.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.db.enums import StoppingRule

PANEL = (
    Path(__file__).resolve().parents[1]
    / "apps"
    / "web"
    / "src"
    / "components"
    / ("StoppingRulesPanel.tsx")
)


def _keys() -> set[str]:
    """The DESCRIPTIONS keys, parsed out of the component.

    Deliberately anchored to the ``const DESCRIPTIONS`` block rather than
    scanning the whole file: a quoted ``"S-01"`` appearing anywhere else must
    not be able to satisfy this test.
    """
    source = PANEL.read_text(encoding="utf-8")
    match = re.search(
        r"const DESCRIPTIONS: Record<string, string> = \{(.*?)\n\};", source, re.DOTALL
    )
    assert match, "the DESCRIPTIONS block is not where this test expects it"
    return set(re.findall(r'"([^"]+)":', match.group(1)))


def test_the_panel_file_exists() -> None:
    """Guards the test itself. A renamed component must fail loudly here
    rather than turn this file into a no-op that always passes."""
    assert PANEL.is_file(), f"{PANEL} not found -- this test would silently stop checking"


def test_every_rule_the_api_sends_has_a_description() -> None:
    wire_ids = {rule.value for rule in StoppingRule}
    missing = wire_ids - _keys()
    assert not missing, (
        f"rules the API sends with no description in the panel: {sorted(missing)}. "
        "They will render as a bare id with blank label text."
    )


def test_no_description_for_a_rule_that_does_not_exist() -> None:
    """The other direction. A stale key is dead code that reads as coverage,
    and is how a rename goes unnoticed."""
    wire_ids = {rule.value for rule in StoppingRule}
    extra = _keys() - wire_ids
    assert not extra, f"descriptions for rules that no longer exist: {sorted(extra)}"


def test_keys_are_wire_values_not_member_names() -> None:
    """The specific mistake, pinned.

    Without this, re-keying the map back onto member names would satisfy both
    tests above only if the enum were also changed — but keying it on member
    names *while* the enum stays as it is would fail them for a reason a reader
    might misdiagnose. This says the thing directly.
    """
    member_names = {rule.name for rule in StoppingRule}
    assert not (_keys() & member_names), (
        "the description map is keyed on Python member names, which never cross "
        "the wire. The API sends rule.value, e.g. 'S-01'."
    )


def test_all_twelve_rules_are_covered() -> None:
    """The count is part of the claim the panel makes: "all twelve listed"."""
    assert len(_keys()) == 12
