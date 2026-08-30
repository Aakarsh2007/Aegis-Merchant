"""Bearer auth for the money-moving and data-reading endpoints (§13.5).

Webhooks are **not** covered here. They authenticate by HMAC signature, because
Razorpay cannot present a bearer token and a shared secret in a header would be
strictly weaker than a signature over the body.

The unset-token decision
------------------------

An API token is not configured out of the box, and Judge Mode is explicitly
required to run with zero credentials (§22). That leaves a choice with a real
cost either way, so it is resolved by environment rather than by a single
convenient default:

* **Not production, no token set** → open, and every response carries
  ``X-Auth-Mode: disabled``, ``/healthz/deep`` reports ``auth: "disabled"``,
  and the app logs a warning at startup. Convenience is allowed to win only
  where it cannot cause harm, and only while saying so.
* **Production, no token set** → :func:`require_api_token` refuses to build and
  the application fails to start.

The second is the important one. The common failure is not "someone chose bad
auth", it is "auth was never configured and nothing said so", and a service
that boots happily with authentication silently disabled is how that happens.
Failing at startup is loud, immediate, and impossible to miss; failing at
request time would leave the endpoints open until someone noticed.
"""

from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request, status

from app.config import Settings, get_settings

__all__ = [
    "Principal",
    "auth_mode",
    "require_api_token",
    "verify_approval_hash",
]

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Principal:
    """Who is acting. Recorded in the audit chain alongside what they did.

    An approval whose audit block says only "approved" answers the wrong
    question. ``§13.5`` requires the approving principal be recorded, so every
    authenticated request produces one of these rather than a bare boolean.
    """

    #: Stable identifier written to ``audit_blocks.actor``.
    name: str
    #: True when no token was configured and the environment permits that.
    #: Carried explicitly so an audit block can never imply an authenticated
    #: human where there was none.
    unauthenticated: bool = False

    @property
    def audit_actor(self) -> str:
        return f"{self.name}(unauthenticated)" if self.unauthenticated else self.name


def auth_mode(settings: Settings | None = None) -> str:
    """``"enforced"`` or ``"disabled"``. Surfaced by ``/healthz/deep``."""
    settings = settings or get_settings()
    return "enforced" if settings.api_token else "disabled"


def _extract_bearer(header: str | None) -> str | None:
    if not header:
        return None
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        return None
    return token.strip()


async def require_api_token(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> Principal:
    """FastAPI dependency. Returns the principal, or raises 401.

    Comparison is :func:`secrets.compare_digest`, not ``==``. A plain string
    comparison returns as soon as it finds a differing byte, so response time
    leaks how many leading characters were correct and a token becomes
    guessable one character at a time. The tokens here are high-entropy enough
    that the attack is impractical, which is an argument for not relying on
    that being true.
    """
    expected = settings.api_token

    if not expected:
        if settings.environment == "production":
            # Unreachable in a correctly-started process: create_app refuses to
            # build in this state. Kept as the second half of a belt-and-braces
            # pair, because the cost of being wrong here is an open API.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="API_TOKEN is not configured; refusing to serve authenticated routes.",
            )
        return Principal(name="anonymous", unauthenticated=True)

    presented = _extract_bearer(authorization)
    if presented is None or not secrets.compare_digest(presented, expected):
        # One message for both "no token" and "wrong token". Distinguishing
        # them tells an attacker which half of the problem they have solved.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid bearer token.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    # A named principal would come from a token->identity mapping in a real
    # deployment. With a single shared token the honest name is the token's
    # role, not an invented human.
    return Principal(name=f"api:{request.client.host if request.client else 'unknown'}")


def verify_approval_hash(*, presented: str, current: str) -> None:
    """Guard the gap between displaying an action and executing it (§13.5).

    A human approves a *specific* action with specific numbers: this customer,
    this discount, this channel. If anything about that action changes between
    the screen they read and the moment execution runs, their approval no
    longer refers to what will happen — and an approval that does not refer to
    a specific action is a button that says "approve", not a control.

    Raises 409 rather than 400: the request was well-formed, the state moved
    underneath it. That distinction tells the operator to re-read and re-approve
    rather than to fix their request.
    """
    if not secrets.compare_digest(presented, current):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "The action changed since it was displayed for approval "
                f"(approved {presented[:12]}..., current {current[:12]}...). "
                "Re-read the proposal and approve again."
            ),
        )
