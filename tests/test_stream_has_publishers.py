"""The live pipeline must have a publisher in the application, not just tests.

INC-038. `EventBus.publish` was called from `tests/test_stream.py` and from
nowhere else in the codebase. So the "Live pipeline" panel connected, sent
heartbeats forever, and displayed nothing — while its own subtitle promised
*"Control-arm holds appear here too — that is the proof they are real."*

A panel that makes a proof claim it can never deliver is worse than no panel.
And this is the **third** time this exact shape has appeared here: INC-024 (a
webhook path that stored events and dropped them), INC-026 (an `llm_calls`
table with a reader and no writer), and now a bus with subscribers and no
publisher. Each was green across the whole suite, because a test that publishes
its own event proves the bus works and says nothing about whether anything uses
it.

So this file asserts on the *application*, not the bus.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "apps" / "api" / "app"


def _app_sources() -> dict[str, str]:
    """Application modules only. The bus's own definition is excluded: a
    `publish` inside `stream.py` is the implementation, not a use of it."""
    return {
        str(path.relative_to(APP)): path.read_text(encoding="utf-8")
        for path in APP.rglob("*.py")
        if path.name != "stream.py"
    }


class TestSomethingActuallyPublishes:
    def test_the_application_publishes_at_least_two_kinds_of_event(self) -> None:
        publishers = {
            name: len(re.findall(r"bus\.publish\(", text))
            for name, text in _app_sources().items()
            if "bus.publish(" in text
        }
        assert publishers, (
            "nothing in the application publishes to the event bus, so the Live "
            "pipeline panel can never show anything (INC-038)"
        )
        assert sum(publishers.values()) >= 2, f"only {publishers} publish"

    def test_the_test_mode_path_publishes(self) -> None:
        """The one path that runs a real case in-process. If it does not
        publish, the demo's best moment produces an empty panel."""
        source = (APP / "routers" / "testmode.py").read_text(encoding="utf-8")
        assert "bus.publish(" in source

    def test_the_webhook_path_publishes_a_verified_recovery(self) -> None:
        """A real signed webhook arriving is the most convincing moment this
        system has. It must reach the screen."""
        source = (APP / "routers" / "webhooks.py").read_text(encoding="utf-8")
        assert "recovery.verified" in source

    def test_the_control_arm_promise_is_kept(self) -> None:
        """The panel's subtitle promises control-arm holds appear. Something has
        to publish `case.control_held` for that to be true."""
        assert any("case.control_held" in text for text in _app_sources().values()), (
            "the panel promises control-arm events that nothing publishes"
        )


class TestEveryPublishedNameIsAllowed:
    """`publish` drops anything not on the allowlist, with a log line nobody
    reads. A typo would be invisible."""

    def _allowlist(self) -> set[str]:
        source = (APP / "routers" / "stream.py").read_text(encoding="utf-8")
        block = re.search(
            r"PUBLIC_EVENTS: frozenset\[str\] = frozenset\((.*?)\n\)", source, re.DOTALL
        )
        assert block, "the allowlist is not where this test expects it"
        return set(re.findall(r'"([a-z_]+\.[a-z_]+)"', block.group(1)))

    def test_the_allowlist_is_readable(self) -> None:
        assert len(self._allowlist()) >= 10

    def test_no_published_literal_is_rejected(self) -> None:
        allowed = self._allowlist()
        published: set[str] = set()
        for text in _app_sources().values():
            published |= set(re.findall(r'bus\.publish\(\s*\n?\s*"([^"]+)"', text))
        rejected = published - allowed
        assert not rejected, (
            f"these event names are published but not on PUBLIC_EVENTS, so they "
            f"are silently dropped: {sorted(rejected)}"
        )

    def test_the_node_event_map_only_names_allowed_events(self) -> None:
        """`_NODE_EVENTS` maps trace node names to event names. Every value must
        be on the allowlist or that node vanishes from the stream."""
        source = (APP / "routers" / "testmode.py").read_text(encoding="utf-8")
        block = re.search(r"_NODE_EVENTS: dict\[str, str\] = \{(.*?)\n\}", source, re.DOTALL)
        assert block, "_NODE_EVENTS is not where this test expects it"
        values = set(re.findall(r':\s*"([^"]+)"', block.group(1)))
        assert values <= self._allowlist(), (
            f"node events not on the allowlist: {sorted(values - self._allowlist())}"
        )


class TestTheUiRendersWhatIsPublished:
    """A publisher and a renderer that disagree is INC-030 again."""

    PANEL = ROOT / "apps" / "web" / "src" / "components" / "PipelineStream.tsx"

    @pytest.mark.parametrize("field", ["node", "summary", "provenance"])
    def test_the_panel_reads_the_trace_fields(self, field: str) -> None:
        """These carry the whole value: which layer answered each step. The
        panel ignored all three and rendered a generic "case diagnosed".

        Matched on the narrowing expression rather than on the field name.
        The first version searched for `data.provenance` anywhere in the file
        and passed when the field was removed from the code, because the word
        still appeared in the comment *explaining why it mattered* — a test
        satisfied by its own documentation.
        """
        source = self.PANEL.read_text(encoding="utf-8")
        assert f'typeof data.{field} === "string"' in source, (
            f"the publisher sends `{field}` and the panel does not read it"
        )
