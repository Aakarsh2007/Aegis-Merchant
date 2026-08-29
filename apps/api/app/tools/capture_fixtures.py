"""Capture real Razorpay Test Mode responses as fixtures.

The deterministic failure classifier (Phase 3) keys on ``error_source`` and
``error_step``. A classifier built against *assumed* field shapes is built on
sand, so this script replaces documented-shape fixtures with responses recorded
from a live Test Mode account, and reports every divergence it finds.

Divergence between documentation and reality is a normal, expected outcome —
and exactly the kind of thing worth writing into ``docs/INCIDENTS.md``.

    python tasks.py capture-fixtures        # needs RAZORPAY_* in .env
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.tools.provider import ProviderError
from app.tools.razorpay_client import RazorpayProvider

FIXTURE_DIR = Path(__file__).resolve().parents[4] / "tests" / "fixtures" / "razorpay"

#: Fields the Phase 3 classifier depends on. Their absence is a finding, not a
#: warning to scroll past.
CRITICAL_FIELDS = ("error_code", "error_source", "error_step", "error_reason", "method")


def _redact(entity: dict[str, Any]) -> dict[str, Any]:
    """Strip contact details before anything is written to a public repo."""
    out = dict(entity)
    for key in ("contact", "email", "vpa", "customer_id"):
        if out.get(key):
            out[key] = "REDACTED"
    return out


async def capture(provider: RazorpayProvider) -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    findings: list[str] = []

    print("Fetching recent payments from Razorpay Test Mode...")
    try:
        body = await provider._request("GET", "/payments", count=100)
    except ProviderError as exc:
        print(f"! could not reach Razorpay: {exc}")
        print("  Check RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET in .env")
        return 1

    items: list[dict[str, Any]] = body.get("items", [])
    failed = [p for p in items if p.get("status") == "failed"]
    print(f"  {len(items)} payments, {len(failed)} failed")

    if not failed:
        print(
            "\n  No failed payments in this Test Mode account yet.\n"
            "  Create one: make a Test Mode payment and choose 'Failure' on the\n"
            "  Razorpay checkout simulator, then re-run this."
        )
        return 0

    observed_sources: set[str] = set()
    observed_steps: set[str] = set()
    for payment in failed:
        observed_sources.add(str(payment.get("error_source")))
        observed_steps.add(str(payment.get("error_step")))
        for field in CRITICAL_FIELDS:
            if field not in payment:
                findings.append(f"payment {payment.get('id')}: missing '{field}'")

    sample = _redact(failed[0])
    out = FIXTURE_DIR / "payment.failed.captured.json"
    out.write_text(
        json.dumps(
            {
                "_fixture_meta": {
                    "provenance": "captured_test_mode",
                    "source": "GET /v1/payments (live Razorpay Test Mode)",
                    "note": "Contact details redacted before writing.",
                    "observed_error_sources": sorted(observed_sources),
                    "observed_error_steps": sorted(observed_steps),
                },
                "entity": "event",
                "event": "payment.failed",
                "contains": ["payment"],
                "payload": {"payment": {"entity": sample}},
                "created_at": sample.get("created_at"),
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"  wrote {out.name}")

    print("\n=== Observed values (compare against app/db/enums.py) ===")
    print(f"  error_source: {sorted(observed_sources)}")
    print(f"  error_step:   {sorted(observed_steps)}")

    if findings:
        print("\n=== DIVERGENCE FROM DOCUMENTED SHAPE ===")
        for f in findings:
            print(f"  ! {f}")
        print("\n  Journal these in docs/INCIDENTS.md -- a field the classifier")
        print("  depends on being absent in practice is a real finding.")
    else:
        print("\n  No divergence: every field the classifier needs is present.")

    return 0


def main() -> int:
    settings = get_settings()
    if not settings.razorpay_live:
        print(
            "RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are not set.\n\n"
            "  1. Sign up free at https://dashboard.razorpay.com\n"
            "  2. Switch the dashboard toggle to *Test Mode*\n"
            "  3. Settings -> API Keys -> Generate Test Key\n"
            "  4. Put both values in .env\n\n"
            "Until then the app runs on the mock provider, which is a fully\n"
            "supported mode -- but these fixtures stay 'documented_shape'."
        )
        return 1

    provider = RazorpayProvider(settings.razorpay_key_id, settings.razorpay_key_secret)
    return asyncio.run(capture(provider))


if __name__ == "__main__":
    raise SystemExit(main())
