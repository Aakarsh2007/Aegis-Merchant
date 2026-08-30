"""RevPilot AI — FastAPI application entrypoint.

Phase 0 scope (workflow.md §27): app factory, health endpoints, and the
dependency wiring that later phases hang off. No business logic yet.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from app.config import Settings, get_settings
from app.core.clock import Clock, SystemClock, iso_ist
from app.deps import get_clock as _deps_get_clock
from app.deps import get_provider
from app.routers import approvals as approvals_router
from app.routers import audit as audit_router
from app.routers import webhooks
from app.security.auth import auth_mode

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

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
        """State the auth posture on every response.

        An open API that looks identical to a secured one is the part worth
        preventing. A client can assert on this header; a human can see it in
        the network tab without reading our configuration.
        """
        response: Response = await call_next(request)
        response.headers["X-Auth-Mode"] = auth_mode(settings)
        return response

    app.include_router(webhooks.router)
    app.include_router(audit_router.router)
    app.include_router(approvals_router.router)

    @app.get("/healthz", tags=["health"], summary="Liveness probe")
    async def healthz() -> dict[str, Any]:
        return {"status": "ok", "service": "revpilot-api", "version": __version__}

    @app.get("/api/v1/health/deep", tags=["health"], summary="Dependency report")
    async def health_deep() -> dict[str, Any]:
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
            "checks": {
                # Populated by their owning phases; declared here so the shape
                # of this endpoint is stable from Phase 0 onward.
                "database": "not_implemented",
                "audit_chain": "implemented: GET /api/v1/audit/verify",
                "outbox_depth": "not_implemented",
                "dlq_depth": "not_implemented",
                "scheduler": "not_implemented",
                "llm_quota_remaining": "not_implemented",
            },
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
