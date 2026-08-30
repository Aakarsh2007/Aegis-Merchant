#!/usr/bin/env python3
"""Cross-platform task runner — the single source of truth for project commands.

Why this exists rather than only a Makefile: `make` is not installed on
Windows, which is the development machine here, and a judge should not need to
install a build tool to run the project. The Makefile delegates to this file,
so `make demo` (mac/linux) and `python tasks.py demo` (anywhere) do exactly
the same thing and cannot drift apart.

    python tasks.py            # list tasks
    python tasks.py test
    python tasks.py demo
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent
API = ROOT / "apps" / "api"
WEB = ROOT / "apps" / "web"
PY = sys.executable

TASKS: dict[str, tuple[str, Callable[[], int]]] = {}


def task(name: str, help_text: str) -> Callable[[Callable[[], int]], Callable[[], int]]:
    def register(fn: Callable[[], int]) -> Callable[[], int]:
        TASKS[name] = (help_text, fn)
        return fn

    return register


def run(cmd: list[str], cwd: Path | None = None, env_extra: dict[str, str] | None = None) -> int:
    env = {**os.environ, **(env_extra or {})}
    printable = " ".join(cmd)
    print(f"\n$ {printable}" + (f"   (cwd={cwd.relative_to(ROOT)})" if cwd else ""))
    return subprocess.call(cmd, cwd=str(cwd or ROOT), env=env)


def _chain(*results: int) -> int:
    """Return the first non-zero exit code, so a failing step is not masked."""
    for r in results:
        if r != 0:
            return r
    return 0


# ---------------------------------------------------------------------------
# setup
# ---------------------------------------------------------------------------
@task("install", "Install API (pip) and web (npm) dependencies")
def install() -> int:
    rc = run([PY, "-m", "pip", "install", "-r", str(API / "requirements.txt")])
    if rc != 0:
        return rc
    if (WEB / "package.json").exists():
        npm = shutil.which("npm")
        if npm is None:
            print("! npm not found; skipping web install")
            return 0
        return run([npm, "install"], cwd=WEB)
    print("• web app not scaffolded yet; skipping")
    return 0


# ---------------------------------------------------------------------------
# quality
# ---------------------------------------------------------------------------
@task("lint", "ruff check + format check")
def lint() -> int:
    return _chain(
        run([PY, "-m", "ruff", "check", "apps/api", "tests", "tasks.py"]),
        run([PY, "-m", "ruff", "format", "--check", "apps/api", "tests", "tasks.py"]),
    )


@task("fmt", "Apply ruff formatting and autofixes")
def fmt() -> int:
    return _chain(
        run([PY, "-m", "ruff", "check", "--fix", "apps/api", "tests", "tasks.py"]),
        run([PY, "-m", "ruff", "format", "apps/api", "tests", "tasks.py"]),
    )


@task("types", "mypy --strict on the API")
def types() -> int:
    return run([PY, "-m", "mypy", "apps/api/app"])


@task("test", "Run the unit and integration suite")
def test() -> int:
    return run([PY, "-m", "pytest"])


@task("eval", "Golden-set + injection-containment suites (cached; no API key)")
def eval_cached() -> int:
    return run([PY, "-m", "pytest", "-m", "eval"])


@task("fuzz", "Property-based policy-firewall proof (hypothesis)")
def fuzz() -> int:
    return run([PY, "-m", "pytest", "-m", "property"])


@task("web-check", "Typecheck and lint the Command Center")
def web_check() -> int:
    """tsc + eslint on the frontend.

    Skipped cleanly when node_modules is absent, so `check` still works on a
    fresh clone where only the Python side has been installed.
    """
    npm = shutil.which("npm")
    if npm is None or not (WEB / "node_modules").exists():
        print("• skipping web checks (run `npm install` in apps/web first)")
        return 0
    npx = shutil.which("npx") or npm
    return _chain(
        run([npx, "tsc", "--noEmit"], cwd=WEB),
        run([npx, "eslint", "src"], cwd=WEB),
    )


@task("check", "Everything CI runs: lint, types, tests, web")
def check() -> int:
    return _chain(lint(), types(), test(), web_check())


# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
@task("api", "Start the API on :8000 (no --reload; see workflow.md §16 #20)")
def api() -> int:
    # Deliberately no --reload: uvicorn's reloader forks the process, which
    # makes the in-process APScheduler run every job twice.
    return run(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=API,
    )


@task("web", "Start the Command Center on :3000")
def web() -> int:
    if not (WEB / "package.json").exists():
        print("• web app not scaffolded yet (Phase 12)")
        return 0
    npm = shutil.which("npm")
    if npm is None:
        print("! npm not found")
        return 1
    return run([npm, "run", "dev"], cwd=WEB)


@task("demo", "One-command judge demo: seed, then start API + web")
def demo() -> int:
    print("\nRevPilot AI — Judge Mode")
    print("  No credentials required. No Docker, Postgres or Redis.")
    print("  Add GEMINI_API_KEY for live reasoning; RAZORPAY_* for real Test Mode links.\n")
    print("• Phases 1-12 not yet built; starting the API only.")
    return api()


# ---------------------------------------------------------------------------
# placeholders — each is implemented by its owning phase
# ---------------------------------------------------------------------------
def _pending(phase: str) -> int:
    print(f"• not implemented yet — {phase}")
    return 0


@task("seed", "Seed the 420-transaction GlowKart corpus (runtime + committed demo DB)")
def seed() -> int:
    """Writes two databases.

    The runtime one is gitignored and disposable. The demo one is committed, so
    a judge running `demo` sees a populated dashboard on first load without
    waiting on a seed step (workflow.md §22).
    """
    rc = run([PY, "-m", "app.db.seed"], cwd=API)
    if rc != 0:
        return rc
    demo_db = ROOT / "data" / "revpilot.seed.db"
    return run([PY, "-m", "app.db.seed", "--out", str(demo_db)], cwd=API)


@task("warm-cache", "Score the model against the baseline and record responses")
def warm_cache() -> int:
    """Needs GEMINI_API_KEY. Run offline, days before a demo -- never on the day.

    Also answers the Phase 6 gate: does the model beat the deterministic rule
    table? Pass `--compare N` to score several candidates first.
    """
    return run([PY, "-m", "app.llm.warm_cache", *sys.argv[2:]], cwd=API)


@task("batch", "Put the seeded corpus through the agent")
def batch() -> int:
    """Create cases, run them through the graph, and settle a proportion.

    Reproducible and re-runnable: it clears its own previous output and seeds
    its RNG. Every settled case is recorded with a `sim_evt_` verifier, so the
    dashboard reports it as SIMULATED and never as RAZORPAY VERIFIED.
    """
    return run([PY, "-m", "app.workers.batch_cli", *sys.argv[2:]], cwd=API)


@task("chaos", "Inject a fault (Phase 13)")
def chaos() -> int:
    return _pending("Phase 13")


@task("verify-audit", "Recompute and verify the SHA-256 audit chain")
def verify_audit() -> int:
    """Verify the chain without starting the API.

    A judge can check the committed database with the server stopped, which
    removes "the running process is lying to you" as an explanation. Exits
    non-zero on a broken chain, so it works in CI unparsed.
    """
    # Absolutised here: the subprocess runs with cwd=apps/api, so a path the
    # user typed relative to the repo root would silently resolve elsewhere.
    paths = [str(Path(arg).resolve()) for arg in sys.argv[2:]]
    return run([PY, "-m", "app.tools.verify_cli", *paths], cwd=API)


@task("openapi", "Export the OpenAPI schema the frontend generates types from")
def openapi() -> int:
    """Write apps/web/openapi.json.

    Committed rather than fetched from a running server, so type generation
    works offline and a contract change shows up as a reviewable diff.
    """
    return run([PY, "-m", "app.tools.export_openapi"], cwd=API)


@task("capture-fixtures", "Record real Razorpay Test Mode responses as fixtures")
def capture_fixtures() -> int:
    """Replace documented-shape fixtures with captured Test Mode responses.

    The deterministic classifier keys on error_source / error_step, so a
    classifier built against assumed field shapes is built on sand. Needs
    RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET in .env.
    """
    return run([PY, "-m", "app.tools.capture_fixtures"], cwd=API)


@task("tunnel", "Expose the local API over HTTPS for real webhooks (Phase 14)")
def tunnel() -> int:
    return _pending("Phase 14")


@task("clean", "Remove caches and build artefacts (never the seed database)")
def clean() -> int:
    targets = [".pytest_cache", ".ruff_cache", ".mypy_cache", ".coverage", "htmlcov"]
    for name in targets:
        path = ROOT / name
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
            print(f"  removed {name}/")
        elif path.exists():
            path.unlink()
            print(f"  removed {name}")
    removed = 0
    for pycache in ROOT.rglob("__pycache__"):
        if "legacy" in pycache.parts or "node_modules" in pycache.parts:
            continue
        shutil.rmtree(pycache, ignore_errors=True)
        removed += 1
    print(f"  removed {removed} __pycache__ dirs")
    return 0


# ---------------------------------------------------------------------------
def usage() -> int:
    print(__doc__ or "")
    print("Tasks:")
    width = max(len(n) for n in TASKS)
    for name, (help_text, _) in TASKS.items():
        print(f"  {name.ljust(width)}  {help_text}")
    return 0


def main(argv: list[str]) -> int:
    if not argv or argv[0] in {"-h", "--help", "help"}:
        return usage()
    name = argv[0]
    if name not in TASKS:
        print(f"unknown task: {name}\n")
        return usage() or 1
    return TASKS[name][1]()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
