"""`python tasks.py chaos <fault>` — inject a fault into the running API.

Talks to the API over HTTP rather than importing the module, because the fault
state is process-local: setting it in this process would have no effect on the
one serving requests. That is the sort of thing that produces ten minutes of
confusion, so it is done the only way that works.
"""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

FAULTS = ("provider_down", "provider_slow", "provider_duplicate", "llm_quota_exhausted", "clear")


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    fault = args[0] if args else "clear"
    base = "http://localhost:8000"

    if fault not in FAULTS:
        print(f"unknown fault {fault!r}. One of: {', '.join(FAULTS)}", file=sys.stderr)
        return 2

    request = urllib.request.Request(
        f"{base}/api/v1/simulation/inject",
        data=json.dumps({"fault": fault}).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            body = json.loads(response.read())
    except urllib.error.URLError as exc:
        print(
            f"cannot reach the API at {base}: {exc}\n  Start it first: python tasks.py api",
            file=sys.stderr,
        )
        return 2

    if fault == "clear":
        print(f"cleared (was {body.get('cleared')})")
    else:
        print(f"injected: {body['active']}")
        print(f"  effect: {body['effect']}")
        print(f"  expect: {body['expected_behaviour']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
