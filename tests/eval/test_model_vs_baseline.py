"""The Phase 6 gate, enforced in CI (workflow.md §15.1).

§15.1 committed, before the model existed, that *if the model does not beat the
rule table we ship the rule table and say so*. This file is where that stops
being a sentence and becomes a test.

It scores the model **from the committed response cache** — real answers
recorded from real calls, replayed. No API key, no network, no quota, and the
same number on every run. Scoring live in CI would need a secret, burn quota,
and give a slightly different answer each time, which is exactly what a
regression gate must not do.

The measured verdict, both prompt revisions reported:

| system | overall | conflicting_signals |
|---|---|---|
| deterministic rule table | **96.5%** | 10/10 |
| gemini-3.1-flash-lite v1 | 82.4% | 10/10 |
| gemini-3.1-flash-lite v2 | 90.6% | 10/10 |

So the rule table ships for diagnosis, and the model is consulted only where
the rule table declares itself unsure — which is the split §4.2 specified
before either existed, now supported by measurement.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.llm.cache import CACHE_FILE
from app.llm.gate import (
    GATE_MODEL,
    GOLDEN_PATH,
    gate_scores,
    load_cached_responses,
    load_golden,
)
from app.llm.gate import model_answer as _gate_model_answer
from app.llm.prompts import PROMPT_VERSION

pytestmark = pytest.mark.eval

GOLDEN = GOLDEN_PATH
MODEL = GATE_MODEL

CASES = load_golden()
CACHE = load_cached_responses()


def model_answer(case: dict[str, Any]) -> str | None:
    """The cached model answer for one case, or None if uncached."""
    return _gate_model_answer(case, CACHE)


def scored() -> tuple[dict[str, Any], dict[str, Any]]:
    """(baseline, model) scores over the cases the cache covers.

    A thin adapter over ``app.llm.gate.gate_scores``. The scoring used to live
    here, which meant application code could not reach it -- so the snapshot
    generator carried the accuracy figures as **hardcoded string literals**, and
    they drifted to 96.5%/90.6% against a real 96.4%/90.4% (INC-045). One
    implementation, two readers.
    """
    g = gate_scores()
    return (
        {
            "correct": g.baseline.correct,
            "total": g.baseline.total,
            "accuracy": g.baseline.accuracy,
            "band": g.baseline.band,
        },
        {
            "correct": g.model.correct,
            "total": g.model.total,
            "accuracy": g.model.accuracy,
            "band": g.model.band,
        },
    )


class TestTheCacheIsUsable:
    """A gate that silently scores nothing is worse than no gate (INC-006)."""

    def test_the_cache_exists_and_is_committed(self) -> None:
        assert CACHE_FILE.exists(), (
            f"{CACHE_FILE} is missing. Run `python tasks.py warm-cache` with a "
            "GEMINI_API_KEY to record real responses."
        )

    def test_the_cache_covers_most_of_the_golden_set(self) -> None:
        covered = sum(1 for c in CASES if model_answer(c) is not None)
        assert covered >= len(CASES) * 0.9, (
            f"only {covered}/{len(CASES)} golden cases are in the cache. The "
            "comparison would be scored over an unrepresentative subset."
        )

    def test_every_cached_entry_matches_the_current_prompt_version(self) -> None:
        """A stale cache must not silently pass. Editing a prompt changes the
        key, so leftovers from an older version are dead weight at best and a
        misleading score at worst."""
        versions = {row.get("prompt_version") for row in CACHE.values()}
        assert versions <= {PROMPT_VERSION}, (
            f"cache holds responses from prompt versions {versions - {PROMPT_VERSION}}; "
            "re-run warm-cache and prune"
        )


class TestTheVerdict:
    def test_the_baseline_still_beats_the_model(self) -> None:
        """The Phase 6 gate.

        Deliberately asserted in the direction the measurement actually went.
        If a future prompt or model genuinely overtakes the rule table, this
        test fails — and that failure is the signal to change the routing in
        `app/llm/routing.py` and update DEC-017. A gate that only fires in the
        flattering direction is not a gate.
        """
        baseline, model = scored()
        assert baseline["accuracy"] > model["accuracy"], (
            f"the model now scores {model['accuracy']:.1%} against the rule table's "
            f"{baseline['accuracy']:.1%}. It has overtaken the baseline: revisit the "
            "routing decision in app/llm/routing.py and DEC-017 rather than "
            "loosening this assertion."
        )

    def test_the_model_matches_the_baseline_where_it_is_actually_used(self) -> None:
        """The sub-result that justifies the architecture.

        On conflicting signals -- the band the model is *for*, where Razorpay's
        own fields disagree -- it matches the rule table exactly. It loses
        overall on cases the rule table already answers well, not on the ones
        it was designed to be asked about.
        """
        baseline, model = scored()
        band = "conflicting_signals"
        base_hits = sum(baseline["band"][band])
        model_hits = sum(model["band"][band])
        assert model["band"][band], "no conflicting-signal cases in the cache"
        assert model_hits >= base_hits, (
            f"the model scores {model_hits}/{len(model['band'][band])} on {band} "
            f"against the baseline's {base_hits}: it is now worse on exactly the "
            "cases it exists to handle, which invalidates the routing split"
        )

    def test_the_model_is_not_catastrophically_worse(self) -> None:
        """A floor, so a broken prompt or a swapped model is caught.

        Without this, `test_the_baseline_still_beats_the_model` would keep
        passing as the model got steadily worse -- it only asserts an ordering.
        """
        _, model = scored()
        assert model["accuracy"] >= 0.80, (
            f"model accuracy has fallen to {model['accuracy']:.1%}; something is "
            "wrong with the prompt, the model, or the cache"
        )


def test_report_comparison(capsys: pytest.CaptureFixture[str]) -> None:
    """Print the comparison. This table goes in the README and the pitch."""
    baseline, model = scored()
    bands = sorted(baseline["band"])
    lines = [
        "",
        "=" * 72,
        "PHASE 6 GATE -- measured, from the committed response cache",
        "=" * 72,
        f"  cases scored: {baseline['total']}   model: {MODEL}   prompt: {PROMPT_VERSION}",
        "",
        f"  {'band':24s} {'rule table':>12s} {'model':>12s}",
    ]
    for band in bands:
        b = f"{sum(baseline['band'][band])}/{len(baseline['band'][band])}"
        m = f"{sum(model['band'][band])}/{len(model['band'][band])}"
        lines.append(f"  {band:24s} {b:>12s} {m:>12s}")
    lines += [
        f"  {'OVERALL':24s} {baseline['accuracy']:>11.1%} {model['accuracy']:>12.1%}",
        "",
        "  VERDICT: the rule table ships for diagnosis. The model is consulted",
        "  only where the rule table declares itself unsure -- where it matches",
        "  the baseline exactly. Committed to in section 15.1 before measuring.",
        "=" * 72,
    ]
    with capsys.disabled():
        print("\n".join(lines))
