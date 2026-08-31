"""Every `tasks.py <command>` a document tells someone to run must exist.

`docs/PITCH.md` instructed a reader — and a judge watching the pitch video — to
run `python tasks.py testmode-recover`, which was not a command. A document
referencing a command that is not there is worse than no document: it fails in
front of the person you wrote it for, at the moment they are deciding whether
the rest of the repo can be trusted.

Cheap to check, and it covers the whole docs surface rather than the one file
that happened to be wrong.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ROOT / "README.md",
    ROOT / "workflow.md",
    ROOT / "docs" / "PITCH.md",
    ROOT / "docs" / "DEPLOYMENT.md",
    ROOT / "docs" / "PRE-REGISTRATION.md",
    ROOT / "docs" / "webhooks.md",
    ROOT / "docs" / "INCIDENTS.md",
    ROOT / "docs" / "DECISIONS.md",
]


def _defined() -> set[str]:
    """Command names, read from the @task decorators rather than from --help.

    Parsing the source avoids importing tasks.py, which would execute its
    module-level path resolution against whatever the cwd happens to be.
    """
    source = (ROOT / "tasks.py").read_text(encoding="utf-8")
    names = set(re.findall(r'@task\(\s*"([a-z][a-z0-9-]*)"', source))
    assert names, "no @task decorators found -- this test has stopped checking anything"
    return names


def _referenced() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for doc in DOCS:
        if not doc.is_file():
            continue
        for name in re.findall(r"tasks\.py ([a-z][a-z0-9-]+)", doc.read_text(encoding="utf-8")):
            found.setdefault(name, []).append(doc.name)
    return found


def test_the_task_list_is_readable() -> None:
    """Guards the test. A refactor that changed the decorator shape would
    otherwise make everything below pass vacuously."""
    assert len(_defined()) >= 20


@pytest.mark.parametrize("name", sorted(_referenced()))
def test_every_documented_command_exists(name: str) -> None:
    where = ", ".join(sorted(set(_referenced()[name])))
    assert name in _defined(), (
        f"`python tasks.py {name}` is referenced in {where} but is not a command. "
        "A doc that tells someone to run a command that does not exist fails in "
        "front of exactly the person it was written for."
    )
