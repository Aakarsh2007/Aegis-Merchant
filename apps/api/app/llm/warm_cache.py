"""Warm the committed response cache, and answer the Phase 6 gate.

Two jobs in one script, because they need the same expensive thing — real model
calls against the golden set:

1. **Score the model** against the deterministic baseline. The commitment in
   §15.1 is enforceable only if measured: *if the model does not beat the rule
   table, we ship the rule table and say so.*
2. **Record every response** into ``data/llm_cache.jsonl``, so the batch demo
   and CI run in seconds with zero API calls and byte-for-byte reproducible
   numbers (§4.5).

Run once, offline, days before submission — never on demo day:

    python tasks.py warm-cache            # score + record
    python tasks.py warm-cache -- --compare   # score several models first
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.agent.classifier import classify
from app.config import get_settings
from app.core.clock import SystemClock
from app.db.enums import LLMSource, LLMTask
from app.llm.cache import CACHE_FILE, CachedAdapter, ResponseCache
from app.llm.gemini_adapter import CANDIDATE_MODELS, GeminiAdapter
from app.llm.rate_limit import RateLimiter

GOLDEN = Path(__file__).resolve().parents[4] / "tests" / "eval" / "golden_diagnoses.jsonl"


def load_golden() -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in GOLDEN.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def context_for(case: dict[str, Any]) -> dict[str, Any]:
    """The redacted context the model sees.

    Exactly what the agent would send: the provider's own error fields and the
    payment method. No customer identity, no amount, no PII (§13.1).
    """
    return {
        "error_source": case["input"]["error_source"],
        "error_step": case["input"]["error_step"],
        "error_reason": case["input"]["error_reason"],
        "method": case["input"]["method"],
        "playbook": "PAYMENT_FAILURE",
    }


def stratified(cases: list[dict[str, Any]], n: int) -> list[dict[str, Any]]:
    """Sample proportionally across difficulty bands.

    ``cases[:n]`` takes the first N, and the golden set is ordered with the
    clean band first -- so a prefix is entirely easy cases, exactly where the
    rule table already scores 100%. A comparison run on that subset cannot
    distinguish any two systems (INC-010).
    """
    by_band: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for case in cases:
        by_band[case["difficulty"]].append(case)

    out: list[dict[str, Any]] = []
    bands = sorted(by_band)
    per_band = max(1, n // len(bands))
    for band in bands:
        out.extend(by_band[band][:per_band])
    # Top up from the hardest bands first: those are where the systems differ.
    for band in reversed(bands):
        for case in by_band[band][per_band:]:
            if len(out) >= n:
                return out[:n]
            out.append(case)
    return out[:n]


def baseline_scores(cases: list[dict[str, Any]]) -> dict[str, Any]:
    """The rule table, for comparison. Free, instant, already measured."""
    correct = 0
    per_band: dict[str, list[bool]] = defaultdict(list)
    for case in cases:
        got = classify(**case["input"])
        ok = got.category.value == case["label"]["category"]
        correct += ok
        per_band[case["difficulty"]].append(ok)
    return {
        "accuracy": correct / len(cases),
        "correct": correct,
        "total": len(cases),
        "per_band": {k: (sum(v), len(v)) for k, v in per_band.items()},
    }


async def score_model(
    model: str,
    cases: list[dict[str, Any]],
    *,
    record_into: ResponseCache | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    settings = get_settings()
    clock = SystemClock()
    subset = stratified(cases, limit) if limit else cases

    live = GeminiAdapter(
        settings.gemini_api_key,
        clock=clock,
        model=model,
        timeout_s=settings.llm_timeout_s,
        rate_limiter=RateLimiter(
            clock=clock, rpm_limit=settings.llm_rpm_limit, rpd_limit=settings.llm_rpd_limit
        ),
        # Wait for a slot rather than degrading. Without this the limiter
        # rejects most calls, they fall back to the rule table, and the
        # "model accuracy" is the rule table's accuracy wearing a hat.
        wait_for_slot_s=90.0,
    )
    adapter = CachedAdapter(
        cache=record_into if record_into is not None else ResponseCache(path=Path("/dev/null")),
        live=live,
        model=model,
        record=record_into is not None,
    )

    correct = 0
    per_band: dict[str, list[bool]] = defaultdict(list)
    latencies: list[int] = []
    tokens_in = tokens_out = 0
    fell_back = 0
    schema_retries = 0
    misses: list[str] = []
    started = time.perf_counter()

    for index, case in enumerate(subset, 1):
        result = await adapter.complete_structured(task=LLMTask.DIAGNOSE, context=context_for(case))
        if result.source is LLMSource.DETERMINISTIC:
            fell_back += 1
        if not result.schema_valid_first_try:
            schema_retries += 1
        if result.source is LLMSource.LIVE:
            latencies.append(result.latency_ms)
            tokens_in += result.input_tokens
            tokens_out += result.output_tokens

        got = str(getattr(result.output, "category", "UNKNOWN"))
        expected = case["label"]["category"]
        ok = got == expected
        correct += ok
        per_band[case["difficulty"]].append(ok)
        if not ok:
            flag = "declared" if case["expected_rule_table_miss"] else "new"
            misses.append(f"    {case['id']} [{flag}] {expected} -> {got}")

        if index % 10 == 0:
            print(f"    {index}/{len(subset)} ...", flush=True)

    latencies.sort()
    return {
        "model": model,
        "accuracy": correct / len(subset),
        "correct": correct,
        "total": len(subset),
        "per_band": {k: (sum(v), len(v)) for k, v in per_band.items()},
        "fell_back": fell_back,
        "schema_retries": schema_retries,
        "median_ms": latencies[len(latencies) // 2] if latencies else 0,
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "wall_s": time.perf_counter() - started,
        "misses": misses,
    }


def report(baseline: dict[str, Any], scored: list[dict[str, Any]]) -> None:
    print("\n" + "=" * 74)
    print("PHASE 6 GATE -- does the model beat the deterministic rule table?")
    print("=" * 74)
    print(
        f"\n  BASELINE (rule table, zero cost)   {baseline['correct']}/{baseline['total']}"
        f" = {baseline['accuracy']:.1%}"
    )
    for row in scored:
        contaminated = row["fell_back"] > 0
        verdict = (
            "UNUSABLE"
            if contaminated
            else "BEATS"
            if row["accuracy"] > baseline["accuracy"]
            else "TIES"
            if row["accuracy"] == baseline["accuracy"]
            else "LOSES TO"
        )
        print(f"\n  {row['model']}")
        print(
            f"    accuracy         {row['correct']}/{row['total']} = {row['accuracy']:.1%}"
            f"   ({verdict} the baseline)"
        )
        for band, (ok, n) in sorted(row["per_band"].items()):
            print(f"      {band:22s} {ok:2d}/{n:2d}")
        print(f"    median latency   {row['median_ms']} ms")
        print(f"    tokens           {row['tokens_in']} in / {row['tokens_out']} out")
        print(f"    fell back        {row['fell_back']}   schema retries {row['schema_retries']}")
        if contaminated:
            print(f"    !! {row['fell_back']}/{row['total']} answers came from the DETERMINISTIC")
            print("       fallback, not the model. This accuracy figure measures the rule")
            print("       table, not the model, and must not be reported (INC-010).")
        print(f"    wall time        {row['wall_s']:.0f}s")
        if row["misses"]:
            print(f"    misclassified ({len(row['misses'])}):")
            for line in row["misses"][:12]:
                print(line)
    print("\n" + "=" * 74)


async def main_async(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not settings.gemini_api_key:
        print("GEMINI_API_KEY is not set. Nothing to warm.\n")
        print("  aistudio.google.com/apikey -> create a key -> put it in .env")
        print("  Everything still runs without it, on the deterministic adapter.")
        return 1

    cases = load_golden()
    baseline = baseline_scores(cases)
    print(f"Loaded {len(cases)} golden cases. Baseline {baseline['accuracy']:.1%}.\n")

    if args.compare:
        scored = []
        for model in CANDIDATE_MODELS:
            print(f"  scoring {model} on {args.compare} cases ...")
            try:
                scored.append(await score_model(model, cases, limit=args.compare))
            except Exception as exc:
                print(f"    unusable: {type(exc).__name__} {str(exc)[:80]}")
        report(baseline, scored)
        return 0

    cache = ResponseCache.load(CACHE_FILE)
    print(f"  cache holds {len(cache)} responses before this run")
    print(f"  scoring and recording {settings.gemini_model} on all {len(cases)} cases ...")
    row = await score_model(settings.gemini_model, cases, record_into=cache)
    cache.save()
    report(baseline, [row])
    print(f"  cache now holds {len(cache)} responses -> {CACHE_FILE}")
    print(f"  by task: {cache.stats()}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Warm the LLM response cache.")
    parser.add_argument(
        "--compare",
        type=int,
        default=0,
        metavar="N",
        help="score N cases across candidate models instead of recording",
    )
    return asyncio.run(main_async(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
