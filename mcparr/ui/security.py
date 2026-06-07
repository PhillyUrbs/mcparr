"""UI security helpers: session auth, CSRF, login throttling, and a Host/Origin guard.

The config UI holds every service API key and can rotate the MCP token, so it is
never left open (security review item #3). Access requires a local admin password;
sessions are signed cookies (SameSite=Strict), state-changing POSTs carry a CSRF
token, and a Host/Origin check mitigates DNS-rebinding.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field

from fastapi import HTTPException, Request, status
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import RedirectResponse, Response

SESSION_USER_KEY = "admin"
SESSION_CSRF_KEY = "csrf"


# --------------------------------------------------------------------------- #
# Session helpers
# --------------------------------------------------------------------------- #


def login_session(request: Request) -> None:
    request.session[SESSION_USER_KEY] = True
    request.session[SESSION_CSRF_KEY] = secrets.token_urlsafe(32)


def logout_session(request: Request) -> None:
    request.session.clear()


def is_authenticated(request: Request) -> bool:
    return bool(request.session.get(SESSION_USER_KEY))


def csrf_token(request: Request) -> str:
    token = request.session.get(SESSION_CSRF_KEY)
    if not token:
        token = secrets.token_urlsafe(32)
        request.session[SESSION_CSRF_KEY] = token
    return token


def require_login(request: Request) -> None:
    """FastAPI dependency: redirect to login when not authenticated."""
    if not is_authenticated(request):
        raise HTTPException(
            status_code=status.HTTP_307_TEMPORARY_REDIRECT,
            headers={"Location": "/login"},
        )


async def verify_csrf(request: Request) -> None:
    """FastAPI dependency: validate the double-submit CSRF token on POST forms."""
    form = await request.form()
    submitted = form.get("csrf_token")
    expected = request.session.get(SESSION_CSRF_KEY)
    if not expected or not submitted or not secrets.compare_digest(str(submitted), expected):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Invalid CSRF token")


def redirect_to_login() -> RedirectResponse:
    return RedirectResponse("/login", status_code=status.HTTP_303_SEE_OTHER)


# --------------------------------------------------------------------------- #
# Login throttling (in-memory)
# --------------------------------------------------------------------------- #


@dataclass
class _Attempts:
    count: int = 0
    locked_until: float = 0.0


@dataclass
class LoginThrottle:
    """Simple per-client failed-login backoff and lockout."""

    max_attempts: int = 5
    lockout_seconds: float = 60.0
    _by_client: dict[str, _Attempts] = field(default_factory=dict)

    def is_locked(self, client: str) -> bool:
        state = self._by_client.get(client)
        return bool(state and state.locked_until > time.monotonic())

    def record_failure(self, client: str) -> None:
        state = self._by_client.setdefault(client, _Attempts())
        state.count += 1
        if state.count >= self.max_attempts:
            state.locked_until = time.monotonic() + self.lockout_seconds
            state.count = 0

    def reset(self, client: str) -> None:
        self._by_client.pop(client, None)


# --------------------------------------------------------------------------- #
# Host/Origin guard (DNS-rebinding mitigation)
# --------------------------------------------------------------------------- #


class HostOriginGuard(BaseHTTPMiddleware):
    """Reject state-changing requests whose Origin does not match the Host.

    This is a lightweight DNS-rebinding / cross-site guard suitable for a
    homelab tool: it does not pin a single hostname (those vary per deployment)
    but ensures an attacker page cannot drive authenticated POSTs.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            if origin:
                host = request.headers.get("host", "")
                origin_host = origin.split("://", 1)[-1]
                if host and origin_host != host:
                    raise HTTPException(
                        status_code=status.HTTP_403_FORBIDDEN,
                        detail="Cross-origin request blocked",
                    )
        return await call_next(request)
