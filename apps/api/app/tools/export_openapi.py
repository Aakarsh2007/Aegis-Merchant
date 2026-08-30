"""Write the OpenAPI schema to disk, for frontend type generation (§19.4).

The Command Center generates its TypeScript types from this file, so the
contract cannot drift silently: rename a field in the API and the frontend
stops compiling, rather than rendering ``undefined`` in a tile during the demo.

Written to a committed file rather than fetched from a running server, so type
generation works offline, in CI, and before the API is up — and so a diff on
this file in review shows exactly what the contract change was.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import Settings
from app.main import create_app

#: Repo-relative. apps/api/app/tools/export_openapi.py -> repo root.
DEFAULT_OUT = Path(__file__).resolve().parents[4] / "apps" / "web" / "openapi.json"


def build_schema() -> dict[str, object]:
    """Build the schema with settings that make it deterministic.

    Credentials are blanked: the schema must not vary by whose ``.env`` is on
    the machine, or the committed file churns on every developer's first run
    and the diff stops meaning anything.
    """
    app = create_app(
        Settings(
            razorpay_key_id="",
            razorpay_key_secret="",
            gemini_api_key="",
            api_token="",
            environment="development",
        )
    )
    return dict(app.openapi())


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    out = Path(args[0]) if args else DEFAULT_OUT
    out.parent.mkdir(parents=True, exist_ok=True)

    schema = build_schema()
    # sort_keys so a field addition produces a small diff rather than a
    # reshuffle, and a trailing newline so the file is POSIX-clean.
    out.write_text(json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    paths = schema.get("paths", {})
    print(f"wrote {out}")
    print(f"  {len(paths) if isinstance(paths, dict) else 0} paths")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
