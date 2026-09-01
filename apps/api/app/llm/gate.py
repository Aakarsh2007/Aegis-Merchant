"""The Phase 6 gate, scored in one place.

Section 15.1 committed, before any measurement, to shipping the deterministic
rule table if the model did not beat it. The model did not beat it. That
measurement is the whole of this project's "we used AI where it helped"
claim, so it is quoted in the README, the pitch, the demo script and the
evidence snapshot.

Why this module exists
----------------------

It was quoted in all four of those places as **96.5% against 90.6% on an
85-case golden set**, and the live measurement is **96.4% against 90.4% over 83
cases**. Two hand-typed decimals and a case count, wrong in the one direction
that matters -- flattering -- and two of them were hardcoded as string literals
*inside the generator of* ``docs/EVIDENCE.md``, the file whose entire claim is
"generated, not written".

The counts differ because the golden set has 85 cases and the committed response
cache covers 83 of them. Scoring only the covered cases is correct -- an
uncached case has no model answer to score -- but it means "85-case golden set"
was never the number the accuracy was computed over. Both figures are now
derived and the covered count travels with them.

The scoring lived in ``tests/eval/test_model_vs_baseline.py``, which application
code cannot import. So it moves here and the test imports it, which is the same
shape as ``tools/docmeta``: one implementation, two readers, no opportunity to
disagree. See INC-045.
"""

from __future__ import annotations

import json
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from app.agent.classifier import classify
from app.db.enums import LLMTask
from app.llm.cache import CACHE_FILE, cache_key
from app.llm.prompts import PROMPT_VERSION

__all__ = [
    "GATE_MODEL",
    "GOLDEN_PATH",
    "ArmScore",
    "GateResult",
    "gate_scores",
    "load_cached_responses",
    "load_golden",
    "model_answer",
]

#: The candidate that lost. Named here rather than in four places.
GATE_MODEL: Final = "gemini-3.1-flash-lite"

GOLDEN_PATH: Final = (
    Path(__file__).resolve().parents[4] / "tests" / "eval" / "golden_diagnoses.jsonl"
)


@dataclass(frozen=True)
class ArmScore:
    """One scorer's result over the covered cases."""

    correct: int
    total: int
    #: Correctness by difficulty band, so the sub-result that justifies the
    #: routing split -- the model matching the rule table on conflicting
    #: signals -- can be read off without rescoring.
    band: dict[str, list[bool]] = field(default_factory=dict)

    @property
    def accuracy(self) -> float:
        return self.correct / self.total if self.total else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "correct": self.correct,
            "total": self.total,
            "accuracy": round(self.accuracy, 4),
            "band": {k: [sum(v), len(v)] for k, v in sorted(self.band.items())},
        }


@dataclass(frozen=True)
class GateResult:
    """Both arms, plus the two counts that were being conflated."""

    baseline: ArmScore
    model: ArmScore
    #: Cases in the golden file.
    golden_cases: int
    #: Cases the committed cache has a model answer for, and therefore the
    #: denominator both accuracies are actually computed over.
    scored_cases: int
    model_name: str = GATE_MODEL

    @property
    def rule_table_wins(self) -> bool:
        return self.baseline.accuracy > self.model.accuracy

    def as_dict(self) -> dict[str, Any]:
        return {
            "baseline": self.baseline.as_dict(),
            "model": self.model.as_dict(),
            "golden_cases": self.golden_cases,
            "scored_cases": self.scored_cases,
            "model_name": self.model_name,
            "rule_table_wins": self.rule_table_wins,
            "basis": (
                f"{self.scored_cases} of {self.golden_cases} golden cases have a "
                "committed model response and are therefore scoreable. An uncached "
                "case has no model answer, so scoring it would be scoring nothing."
            ),
        }


def load_golden() -> list[dict[str, Any]]:
    if not GOLDEN_PATH.is_file():
        return []
    return [
        json.loads(line)
        for line in GOLDEN_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def load_cached_responses() -> dict[str, dict[str, Any]]:
    if not CACHE_FILE.exists():
        return {}
    out: dict[str, dict[str, Any]] = {}
    for line in CACHE_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["cache_key"]] = row
    return out


def _context_for(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "error_source": case["input"]["error_source"],
        "error_step": case["input"]["error_step"],
        "error_reason": case["input"]["error_reason"],
        "method": case["input"]["method"],
        "playbook": "PAYMENT_FAILURE",
    }


def model_answer(case: dict[str, Any], cache: dict[str, dict[str, Any]]) -> str | None:
    key = cache_key(
        task=LLMTask.DIAGNOSE,
        model=GATE_MODEL,
        prompt_version=PROMPT_VERSION,
        context=_context_for(case),
    )
    row = cache.get(key)
    return str(row["response"]["category"]) if row else None


def gate_scores() -> GateResult:
    """Score the rule table and the model over the cases the cache covers.

    Deterministic and offline: the model's answers come from the committed
    response cache, never from a live call. Two runs a week apart produce the
    same figure, which is the only way a quoted accuracy can be checked.
    """
    cases = load_golden()
    cache = load_cached_responses()
    covered = [c for c in cases if model_answer(c, cache) is not None]

    base_ok = model_ok = 0
    base_band: dict[str, list[bool]] = defaultdict(list)
    model_band: dict[str, list[bool]] = defaultdict(list)
    for case in covered:
        expected = case["label"]["category"]
        hit_base = classify(**case["input"]).category.value == expected
        hit_model = model_answer(case, cache) == expected
        base_ok += hit_base
        model_ok += hit_model
        base_band[case["difficulty"]].append(hit_base)
        model_band[case["difficulty"]].append(hit_model)

    return GateResult(
        baseline=ArmScore(correct=base_ok, total=len(covered), band=dict(base_band)),
        model=ArmScore(correct=model_ok, total=len(covered), band=dict(model_band)),
        golden_cases=len(cases),
        scored_cases=len(covered),
    )
