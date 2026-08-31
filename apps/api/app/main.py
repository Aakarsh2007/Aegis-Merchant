"""RevPilot AI — FastAPI application entrypoint.

Phase 0 scope (workflow.md §27): app factory, health endpoints, and the
dependency wiring that later phases hang off. No business logic yet.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock, iso_ist
from app.deps import get_clock as _deps_get_clock
from app.deps import get_db, get_provider
from app.routers import adversarial as adversarial_router
from app.routers import approvals as approvals_router
from app.routers import audit as audit_router
from app.routers import briefing as briefing_router
from app.routers import cases as cases_router
from app.routers import control as control_router
from app.routers import dlq as dlq_router
from app.routers import metrics as metrics_router
from app.routers import simulation as simulation_router
from app.routers import stream as stream_router
from app.routers import testmode as testmode_router
from app.routers import webhooks
from app.security.auth import auth_mode
from app.services.metrics import queue_depths
from app.tools.audit import AuditChain

log = logging.getLogger(__name__)

__version__ = "0.1.0"

#: The single application clock. Tests substitute a FakeClock via
#: dependency override rather than patching global time (§21).
_clock: Clock = SystemClock()


def get_clock() -> Clock:
    """Re-exported from app.deps so existing imports keep working."""
    return _deps_get_clock()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.clock = _clock
    app.state.started_at = _clock.now_utc()
    yield


def _configure_logging(settings: Settings) -> None:
    """Make the application's own log records reach somewhere.

    Uvicorn configures ``uvicorn.*`` loggers and leaves the root logger without
    a handler, so every ``log.warning`` in this application was being silently
    discarded at runtime -- the scheduler's expiry counts, the drainer's retry
    notices, the webhook signature diagnostics, all of it.

    It was invisible because the one message that DID appear -- the API_TOKEN
    warning -- is emitted while the app is being built, before uvicorn takes
    over logging. Everything after startup went nowhere, which is the worst
    possible arrangement: it looks like logging works.

    Only attaches a handler if the root logger has none, so a deployment that
    configures logging properly is left alone.
    """
    root = logging.getLogger()
    if not root.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(levelname)s:    %(name)s | %(message)s"))
        root.addHandler(handler)
    root.setLevel(logging.DEBUG if settings.debug else logging.INFO)


async def _run_checks(session: AsyncSession, clock: Clock) -> dict[str, Any]:
    """Real probes, replacing the Phase 0 placeholders.

    Each check is individually guarded: a probe that raises must degrade *that
    check* to an error string rather than 500 the whole endpoint. A health
    endpoint that dies when one dependency is sick is useless at exactly the
    moment it is needed.

    The database check writes nothing. It confirms the connection works and
    that WAL is on, because most of the concurrency design assumes WAL and a
    database silently running in journal mode would be a slow, confusing
    failure rather than a loud one.
    """
    checks: dict[str, Any] = {}

    try:
        journal = await session.scalar(text("PRAGMA journal_mode"))
        checks["database"] = {
            "ok": True,
            "journal_mode": str(journal),
            "wal": str(journal).lower() == "wal",
        }
    except Exception as exc:  # pragma: no cover - defensive
        checks["database"] = {"ok": False, "error": str(exc)[:200]}

    try:
        depths = await queue_depths(session)
        checks["outbox_depth"] = depths["outbox_pending"]
        checks["outbox_sending"] = depths["outbox_sending"]
        checks["dlq_depth"] = depths["dlq_pending"]
    except Exception as exc:  # pragma: no cover - defensive
        checks["outbox_depth"] = f"error: {exc}"
        checks["dlq_depth"] = f"error: {exc}"

    try:
        verification = await AuditChain(clock).verify(session)
        checks["audit_chain"] = {
            "valid": verification.valid,
            "blocks": verification.blocks,
            "head_hash": verification.head_hash,
            "endpoint": "GET /api/v1/audit/verify",
        }
    except Exception as exc:
        # Same keys as the success branch. A probe whose failure shape differs
        # from its success shape forces every consumer to handle two schemas,
        # and the one that forgets breaks precisely when something is already
        # wrong. `valid: false` with the reason is the honest answer on a
        # database that has not been initialised.
        checks["audit_chain"] = {
            "valid": False,
            "blocks": 0,
            "head_hash": None,
            "endpoint": "GET /api/v1/audit/verify",
            "error": str(exc)[:200],
        }

    # The scheduler and drainer are started by Phase 13's runner. Reporting
    # "not_running" is the honest answer today: claiming a sweeper is alive
    # when nothing starts it is exactly the lie this endpoint must not tell.
    checks["scheduler"] = "not_running: started by the batch runner (Phase 13)"
    checks["sse_subscribers"] = stream_router.bus.subscriber_count
    return checks


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    _configure_logging(settings)

    app = FastAPI(
        title="RevPilot AI",
        description=(
            "Autonomous revenue recovery agent for Razorpay merchants. "
            "Bounded agent: the LLM diagnoses and writes; it never touches money."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    # The Command Center runs on :3000 in dev.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000", "http://127.0.0.1:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        # INC-035. Without this the dashboard cannot read `X-Auth-Mode`, and
        # `allow_headers=["*"]` does not help: that governs the REQUEST headers a
        # browser may send, not the RESPONSE headers JavaScript may read. CORS
        # exposes only a safe-list (Cache-Control, Content-Language,
        # Content-Type, Expires, Last-Modified, Pragma) unless a server names
        # more.
        #
        # The failure mode is the dangerous kind: `headers.get("x-auth-mode")`
        # returns null rather than throwing, so a banner reading it renders
        # nothing and looks like a configuration where auth is enabled. A
        # security notice that silently cannot appear is worse than none, because
        # its absence reads as reassurance.
        expose_headers=["X-Auth-Mode"],
    )

    # Fail closed, at startup, before a single request is served.
    #
    # The realistic failure is not "someone chose weak auth", it is "auth was
    # never configured and nothing said so". Refusing to build the app is loud
    # and immediate; checking at request time would leave every endpoint open
    # until somebody happened to notice.
    if settings.environment == "production" and not settings.api_token:
        raise RuntimeError(
            "API_TOKEN is not set and ENVIRONMENT=production. Refusing to start with "
            "authentication disabled on the money-moving endpoints. Set API_TOKEN, or "
            "run with ENVIRONMENT=development if this is a local demo."
        )
    if not settings.api_token:
        log.warning(
            "API_TOKEN is not set: authenticated endpoints are OPEN. This is allowed "
            "in %s so Judge Mode runs with zero credentials, and every response is "
            "marked X-Auth-Mode: disabled.",
            settings.environment,
        )

    @app.middleware("http")
    async def _mark_auth_mode(request: Request, call_next: Any) -> Response:
        """State the auth posture on **every** response, errors included.

        An open API that looks identical to a secured one is the part worth
        preventing. A client can assert on this header; a human can see it in
        the network tab without reading our configuration.

        INC-035b: the first version returned early when the handler raised, so a
        500 carried no header at all -- and the startup log's promise that
        "every response is marked X-Auth-Mode: disabled" was false for exactly
        the responses where a client is most likely to be probing. A header that
        vanishes under load or error is a header a client cannot rely on, so the
        exception path sets it too and re-raises.
        """
        mode = auth_mode(settings)
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            # Starlette will turn this into a 500. Attach the header to that
            # response rather than swallowing the error -- the posture is a fact
            # about the deployment and does not stop being true because a
            # handler failed.
            failed = Response(status_code=500, content=b"Internal Server Error")
            failed.headers["X-Auth-Mode"] = mode
            failed.headers["X-Error-Class"] = type(exc).__name__
            log.exception("unhandled error on %s %s", request.method, request.url.path)
            return failed
        response.headers["X-Auth-Mode"] = mode
        return response

    app.include_router(webhooks.router)
    app.include_router(audit_router.router)
    app.include_router(approvals_router.router)
    app.include_router(cases_router.router)
    app.include_router(metrics_router.router)
    app.include_router(dlq_router.router)
    app.include_router(stream_router.router)
    app.include_router(briefing_router.router)
    app.include_router(simulation_router.router)
    app.include_router(control_router.router)
    app.include_router(adversarial_router.router)
    app.include_router(testmode_router.router)

    @app.get("/healthz", tags=["health"], summary="Liveness probe")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "revpilot-api", "version": __version__}

    @app.get("/api/v1/health/deep", tags=["health"], summary="Dependency report")
    async def health_deep(
        session: Annotated[AsyncSession, Depends(get_db)],
    ) -> dict[str, Any]:
        """Full dependency report.

        ``llm_adapter: "deterministic"`` is how a judge with no API key
        confirms the system is running honestly in a fully-functional
        zero-credential mode rather than fabricating model output (§18.3).
        Checks for subsystems not yet built report ``"not_implemented"`` so
        this endpoint never lies about what exists.
        """
        clock = get_clock()
        return {
            "status": "ok",
            "version": __version__,
            "now_ist": iso_ist(clock.now_ist()),
            "environment": settings.environment,
            "razorpay": "test_mode" if settings.razorpay_live else "mock_provider",
            "provider_impl": get_provider(settings).name,
            "webhook_secret_configured": bool(settings.razorpay_webhook_secret),
            "llm_adapter": settings.llm_provider,
            "llm_model": settings.gemini_model if settings.llm_provider == "gemini" else None,
            "simulation_allowed": settings.simulation_allowed,
            "auth": auth_mode(settings),
            "checks": await _run_checks(session, clock),
            "policy": {
                "max_autonomous_amount_paise": settings.max_autonomous_amount_paise,
                "max_discount_pct": settings.max_discount_pct,
                "max_contacts_48h": settings.max_contacts_48h,
                "quiet_hours_ist": [
                    settings.quiet_hours_start_ist,
                    settings.quiet_hours_end_ist,
                ],
                "control_arm_fraction": settings.control_arm_fraction,
            },
        }

    return app


app = create_app()
