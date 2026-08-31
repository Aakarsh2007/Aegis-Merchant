"""The dashboard must be able to *read* the auth-mode header.

INC-035. The API sets `X-Auth-Mode: disabled` on every response and CORS
allowed the dashboard's origin, so the header was plainly visible in curl. It
was **not** readable from JavaScript.

CORS exposes only a safe-list of response headers to script -- Cache-Control,
Content-Language, Content-Type, Expires, Last-Modified, Pragma -- unless the
server names more via `Access-Control-Expose-Headers`. And
`allow_headers=["*"]`, which the config already had, does not help: that governs
which *request* headers a browser may send, not which *response* headers script
may read. Two similarly-named settings, opposite directions.

The failure mode is the dangerous kind. `headers.get("x-auth-mode")` returns
null rather than throwing, so the banner warning that authentication is off
would render nothing -- and its absence reads as "auth is on". A security notice
that silently cannot appear is worse than no notice at all.

Caught by checking the response headers a browser would actually receive rather
than the ones curl prints.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

ORIGIN = "http://localhost:3000"

#: Readable from script without being named. Everything else needs exposing.
CORS_SAFELISTED = {
    "cache-control",
    "content-language",
    "content-type",
    "expires",
    "last-modified",
    "pragma",
}


def _app(**over: object) -> TestClient:
    settings = Settings(
        environment="development", database_url="sqlite+aiosqlite:///:memory:", **over
    )  # type: ignore[arg-type]
    return TestClient(create_app(settings))


class TestTheHeaderIsSent:
    def test_auth_mode_is_disabled_without_a_token(self) -> None:
        with _app() as client:
            response = client.get("/healthz")
        assert response.headers.get("x-auth-mode") == "disabled"

    def test_auth_mode_is_enabled_with_a_token(self) -> None:
        with _app(api_token="x" * 40) as client:
            response = client.get("/healthz")
        assert response.headers.get("x-auth-mode") != "disabled"


class TestTheHeaderIsReadableFromScript:
    """**The bug.** Sent is not the same as readable."""

    def test_expose_headers_names_x_auth_mode(self) -> None:
        with _app() as client:
            response = client.get("/healthz", headers={"Origin": ORIGIN})
        exposed = {
            h.strip().lower()
            for h in (response.headers.get("access-control-expose-headers") or "").split(",")
            if h.strip()
        }
        assert "x-auth-mode" in exposed, (
            "X-Auth-Mode is sent but not exposed, so the dashboard's "
            "headers.get() returns null and the 'authentication is off' banner "
            "silently never renders. Its absence reads as reassurance."
        )

    def test_the_origin_is_allowed(self) -> None:
        """Guards the test above: with no CORS at all, `expose_headers` would be
        absent for a reason that has nothing to do with this bug."""
        with _app() as client:
            response = client.get("/healthz", headers={"Origin": ORIGIN})
        assert response.headers.get("access-control-allow-origin") == ORIGIN

    def test_x_auth_mode_is_not_safelisted(self) -> None:
        """The premise, asserted so the test cannot become a tautology.

        If `X-Auth-Mode` were a safe-listed header, exposing it would be
        unnecessary and this whole file pointless. It is not, and writing that
        down is cheaper than someone rediscovering the CORS spec later.
        """
        assert "x-auth-mode" not in CORS_SAFELISTED

    def test_the_header_survives_a_failing_request(self) -> None:
        """The middleware must not be bypassed when a handler raises.

        `/api/v1/metrics/overview` against an empty in-memory database returns a
        500. The auth-mode header still has to be there: a dashboard that read it
        only on healthy responses would stop warning about open auth at exactly
        the moment the system was misbehaving.
        """
        settings = Settings(environment="development", database_url="sqlite+aiosqlite:///:memory:")
        # `raise_server_exceptions=False`, or TestClient re-raises the handler's
        # exception and there is no response left to inspect.
        with TestClient(create_app(settings), raise_server_exceptions=False) as client:
            response = client.get("/api/v1/metrics/overview", headers={"Origin": ORIGIN})
        assert response.status_code == 500, "expected the empty database to fail"
        assert response.headers.get("x-auth-mode") == "disabled"
