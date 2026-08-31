"""`python tasks.py testmode-recover` — one real Test Mode recovery.

Calls the running API rather than reimplementing the endpoint, so the CLI and
the dashboard button cannot drift apart. Needs the API up (`python tasks.py
api`) and Razorpay keys configured.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

from app.config import get_settings

DEFAULT_BASE = "http://127.0.0.1:8000"


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    base = args[0].rstrip("/") if args else DEFAULT_BASE
    settings = get_settings()

    request = urllib.request.Request(
        f"{base}/api/v1/testmode/recover",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    if settings.api_token:
        request.add_header("Authorization", f"Bearer {settings.api_token}")

    try:
        with urllib.request.urlopen(request, timeout=40) as response:
            body = json.load(response)
    except urllib.error.HTTPError as exc:
        print(f"the API refused: {exc.code} {exc.reason}", file=sys.stderr)
        print(exc.read().decode("utf-8", "replace")[:500], file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"cannot reach the API at {base}: {exc.reason}", file=sys.stderr)
        print("Start it with `python tasks.py api`.", file=sys.stderr)
        return 1

    if body.get("stopped_before_execution"):
        # A refusal is a legitimate outcome, not a failure. Exit 0.
        print("The policy firewall refused. NOTHING WAS SENT.")
        print(f"  case          {body.get('case_id')}")
        print(f"  status        {body.get('status')}")
        print(f"  stopping rule {body.get('stopping_rule')}")
        for reason in body.get("block_reasons") or []:
            print(f"  reason        {reason}")
        return 0

    print("=" * 70)
    print("  A REAL RAZORPAY TEST MODE LINK")
    print("=" * 70)
    print(f"  case        {body.get('case_id')}")
    print(f"  diagnosis   {body.get('diagnosis')}  ({body.get('diagnosis_source')})")
    print(f"  strategy    {body.get('strategy')}")
    print(f"  reference   {body.get('reference_id')}")
    print(f"  link id     {body.get('razorpay_link_id')}")
    print()
    print(f"  PAY HERE    {body.get('pay_url')}")
    print()
    print("  Card 4111 1111 1111 1111, any future expiry, any CVV, then Success.")
    print(f"  {body.get('next_step', '')}")
    print("=" * 70)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
