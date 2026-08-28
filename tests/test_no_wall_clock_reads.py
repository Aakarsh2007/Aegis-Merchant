"""Executable lint rule: no module may read the wall clock directly.

workflow.md §21 requires every time read to go through ``clock.now_ist()``.
This is enforced as a test rather than a convention, because a single
``datetime.now()`` in the quiet-hours check would make that rule untestable
and could send a customer a message at 2 AM.

Implemented with the ``ast`` module rather than a regex so that a string or a
comment mentioning ``datetime.now()`` — this docstring, for instance — does
not trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1] / "apps" / "api" / "app"

#: The single sanctioned wall-clock reader.
ALLOWED = {APP_ROOT / "core" / "clock.py"}

FORBIDDEN_ATTRS = {
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("datetime", "today"),
    ("date", "today"),
    ("time", "time"),
}
FORBIDDEN_NAMES = {"utcnow"}


def _python_files() -> list[Path]:
    return sorted(p for p in APP_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func

        if isinstance(func, ast.Attribute):
            owner = func.value
            owner_name = (
                owner.id
                if isinstance(owner, ast.Name)
                else owner.attr
                if isinstance(owner, ast.Attribute)
                else None
            )
            if owner_name and (owner_name, func.attr) in FORBIDDEN_ATTRS:
                found.append(f"{path.name}:{node.lineno} {owner_name}.{func.attr}()")

        elif isinstance(func, ast.Name) and func.id in FORBIDDEN_NAMES:
            found.append(f"{path.name}:{node.lineno} {func.id}()")

    return found


def test_app_directory_is_present() -> None:
    """Guard against the scan silently passing because the path is wrong."""
    assert APP_ROOT.is_dir(), f"missing app root: {APP_ROOT}"
    assert _python_files(), "no python files found to scan"


def test_no_module_reads_the_wall_clock_directly() -> None:
    offenders: list[str] = []
    for path in _python_files():
        if path in ALLOWED:
            continue
        offenders.extend(_violations(path))

    assert not offenders, (
        "Direct wall-clock reads found. Inject a Clock and call "
        "clock.now_ist() / clock.now_utc() instead (workflow.md §21):\n  " + "\n  ".join(offenders)
    )


def test_the_rule_can_actually_fail(tmp_path: Path) -> None:
    """Meta-test: prove the checker detects a violation.

    A lint rule that always passes is worse than no lint rule, because it
    reads as evidence in CI while checking nothing.
    """
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from datetime import datetime\ndef when():\n    return datetime.now()\n",
        encoding="utf-8",
    )
    assert _violations(bad), "checker failed to flag a real violation"


def test_the_rule_ignores_mentions_in_strings_and_comments(tmp_path: Path) -> None:
    ok = tmp_path / "ok.py"
    ok.write_text(
        '"""Do not call datetime.now() here."""\n'
        "# datetime.utcnow() is banned\n"
        "MESSAGE = 'datetime.now()'\n"
        "def when(clock):\n"
        "    return clock.now_ist()\n",
        encoding="utf-8",
    )
    assert not _violations(ok), "checker false-positived on a string or comment"


def test_clock_module_is_the_only_exemption() -> None:
    """If the allow-list ever grows, that should be a deliberate decision."""
    assert {APP_ROOT / "core" / "clock.py"} == ALLOWED
