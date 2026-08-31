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
import sqlite3
import subprocess
import sys
import time
import urllib.request
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
        print("! the dashboard is not scaffolded; see apps/web/README or docs")
        return 0
    npm = shutil.which("npm")
    if npm is None:
        print("! npm not found")
        return 1
    return run([npm, "run", "dev"], cwd=WEB)


@task("demo", "One command: seed, run the batch, start the API and the dashboard")
def demo() -> int:
    """Everything a judge needs, in one command.

    Seeds only if the database is absent, and runs the batch only if there are
    no cases — so re-running is fast and produces identical numbers rather than
    doubled ones.

    The API starts as a child process and the dashboard runs in the foreground,
    so Ctrl+C stops both. Two terminals work equally well and are what the
    README suggests for a demo you want to keep control of.
    """
    print()
    print("  RevPilot AI - Judge Mode")
    print("  No credentials required. No Docker, Postgres, Redis or Kafka.")
    print("  Add GEMINI_API_KEY for live reasoning; RAZORPAY_* for real Test Mode links.")
    print()

    runtime_db = API / "revpilot.db"
    if not runtime_db.exists():
        seed_db = ROOT / "data" / "revpilot.seed.db"
        if seed_db.exists():
            shutil.copy(seed_db, runtime_db)
            print("  [1/4] copied the committed demo database")
        else:
            print("  [1/4] seeding the 420-transaction corpus ...")
            if seed() != 0:
                return 1
    else:
        print("  [1/4] database present")

    with sqlite3.connect(runtime_db) as conn:
        try:
            # Count only NON-DEMO cases. A single Test Mode case
            # (`testmode/recover`, is_demo=1) used to satisfy `count > 0` and
            # skip the batch entirely, leaving a dashboard of zeroes with one
            # row in it -- which is exactly what a judge would have seen.
            cases = conn.execute(
                "select count(*) from recovery_cases where is_demo = 0"
            ).fetchone()[0]
        except sqlite3.OperationalError:
            cases = 0
    if cases == 0:
        print("  [2/4] running the corpus through the agent (~3 s, no API calls) ...")
        if run([PY, "-m", "app.workers.batch_cli"], cwd=API) != 0:
            return 1
    else:
        print(f"  [2/4] {cases} cases already present; skipping the batch")

    npm = shutil.which("npm")
    if npm is None or not (WEB / "node_modules").exists():
        print("  [3/4] dashboard dependencies missing - run `npm install` in apps/web")
        print("  [4/4] starting the API only")
        print()
        print("        API docs   http://localhost:8000/docs")
        print()
        return api()

    print("  [3/4] starting the API on :8000 ...")
    api_process = subprocess.Popen(
        [PY, "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"],
        cwd=API,
    )
    try:
        for _ in range(40):
            try:
                with urllib.request.urlopen("http://localhost:8000/healthz", timeout=1):
                    break
            except Exception:
                time.sleep(0.5)
        else:
            print("! the API did not come up; see the output above")
            return 1

        print("  [4/4] starting the dashboard on :3000 ...")
        print()
        print("        Dashboard    http://localhost:3000")
        print("        API docs     http://localhost:8000/docs")
        print("        Verify chain http://localhost:8000/api/v1/audit/verify")
        print()
        print("        Ctrl+C stops both.")
        print()
        return run([npm, "run", "dev"], cwd=WEB)
    finally:
        api_process.terminate()
        try:
            api_process.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover
            api_process.kill()


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


@task("chaos", "Inject a fault into the running API")
def chaos() -> int:
    """Inject or clear a fault. Requires the API to be running.

    `python tasks.py chaos provider_down` / `python tasks.py chaos clear`
    """
    fault = sys.argv[2] if len(sys.argv) > 2 else "clear"
    return run([PY, "-m", "app.tools.chaos_cli", fault], cwd=API)


@task("reconcile", "Ask Razorpay which of our links were actually paid")
def reconcile() -> int:
    """Settle outstanding references by reading Razorpay directly.

    The webhook path is a notification and can be lost; this is the backstop
    production needs anyway. It also means the Test Mode demo needs no tunnel:
    create a link, pay it, run this.
    """
    return run([PY, "-m", "app.workers.reconcile_cli"], cwd=API)


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


@task("testmode-experiment", "A real randomised holdout against Razorpay Test Mode")
def testmode_experiment() -> int:
    """Both arms, real provider. Needs Razorpay keys and a tunnel.

    Not part of `demo`: it makes live provider calls, and a judge running the
    demo should get the offline reproducible path.
    """
    return run([PY, "-m", "app.workers.experiment_cli", *sys.argv[2:]], cwd=API)


@task("power", "How many cases the causal question needs, and how far short we are")
def power() -> int:
    """Print the arithmetic behind PRE-REGISTRATION.md section 5.

    So the figures in that document are reproducible by a judge rather than
    taken on trust, and so the gap between "not statistically significant" and
    an actual answer is a number rather than an adjective.
    """
    return run([PY, "-m", "app.tools.power_cli", *sys.argv[2:]], cwd=API)


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


@task("tunnel", "Expose the local API over HTTPS for real webhooks")
def tunnel() -> int:
    """Start a Cloudflare quick tunnel and print the registration steps.

    No account, no card, no configuration. The URL is ephemeral and changes on
    every run, which is the correct trade for a demo and is stated plainly
    rather than discovered when a webhook stops arriving.
    """
    return run([PY, "-m", "app.tools.tunnel", *sys.argv[2:]], cwd=API)


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
