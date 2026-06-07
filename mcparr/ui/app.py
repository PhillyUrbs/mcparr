"""Config UI application factory (port 7475).

Assembles the auth-gated FastAPI app: first-run admin password setup, login,
the service dashboard, per-instance add/edit/delete/toggle/test, and the Connect
page (MCP URL, token, per-client snippets, token rotation). All sensitive routes
require a session; state-changing POSTs require a CSRF token.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from .. import __version__
from ..config import Database
from ..health import add_health_routes
from ..logging import AuditLog
from ..service_manager import ServiceManager
from ..services.base import list_service_types
from . import security as sec
from .i18n import (
    LOCALE_COOKIE,
    SUPPORTED_LOCALES,
    get_translator,
    message_for,
    resolve_locale,
)

logger = logging.getLogger("mcparr.ui")

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


@dataclass
class UISettings:
    """Runtime knobs the UI needs that live outside the database."""

    mcp_port: int
    get_token: Callable[[], str]
    rotate_token: Callable[[], str]
    token_from_env: bool = False


def create_ui_app(
    *,
    db: Database,
    manager: ServiceManager,
    audit: AuditLog,
    session_secret: str,
    settings: UISettings,
) -> FastAPI:
    app = FastAPI(title="mcparr", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(sec.HostOriginGuard)
    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        same_site="strict",
        https_only=False,  # homelab default; TLS is handled by a reverse proxy
    )
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))
    throttle = sec.LoginThrottle()
    add_health_routes(app, db, ready_check=lambda: True)

    # ------------------------------------------------------------------ #
    # Rendering helper - injects the per-request translator and globals.
    # ------------------------------------------------------------------ #

    def render(request: Request, template: str, **context: object) -> HTMLResponse:
        lang = resolve_locale(
            query=request.query_params.get("lang"),
            cookie=request.cookies.get(LOCALE_COOKIE),
            accept_language=request.headers.get("accept-language"),
            default=db.get_language(),
        )
        ctx: dict[str, object] = {
            "request": request,
            "_": get_translator(lang),
            "lang": lang,
            "msg": message_for,
            "supported_locales": SUPPORTED_LOCALES,
            "version": __version__,
            "csrf_token": sec.csrf_token(request),
            "authenticated": sec.is_authenticated(request),
            **context,
        }
        response = templates.TemplateResponse(request, template, ctx)
        if request.query_params.get("lang") and lang in SUPPORTED_LOCALES:
            response.set_cookie(LOCALE_COOKIE, lang, samesite="strict", httponly=True)
        return response

    def client_id(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    # ------------------------------------------------------------------ #
    # First-run setup + auth
    # ------------------------------------------------------------------ #

    @app.get("/setup", response_class=HTMLResponse, response_model=None)
    async def setup_form(request: Request) -> Response:
        if db.is_admin_password_set():
            return sec.redirect_to_login()
        return render(request, "setup.html")

    @app.post("/setup", response_model=None)
    async def setup_submit(
        request: Request,
        password: str = Form(...),
        confirm: str = Form(...),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> Response:
        if db.is_admin_password_set():
            return sec.redirect_to_login()
        if len(password) < 8 or password != confirm:
            return render(request, "setup.html", error="error.password_mismatch")
        db.set_admin_password(password)
        sec.login_session(request)
        audit.record("admin.password_set")
        return RedirectResponse("/", status_code=303)

    @app.get("/login", response_class=HTMLResponse, response_model=None)
    async def login_form(request: Request) -> HTMLResponse | RedirectResponse:
        if not db.is_admin_password_set():
            return RedirectResponse("/setup", status_code=303)
        if sec.is_authenticated(request):
            return RedirectResponse("/", status_code=303)
        return render(request, "login.html")

    @app.post("/login", response_model=None)
    async def login_submit(
        request: Request,
        password: str = Form(...),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> HTMLResponse | RedirectResponse:
        client = client_id(request)
        if throttle.is_locked(client):
            audit.record("admin.login_locked", client=client)
            return render(request, "login.html", error="error.locked_out")
        if db.verify_admin_password(password):
            throttle.reset(client)
            sec.login_session(request)
            audit.record("admin.login_success", client=client)
            return RedirectResponse("/", status_code=303)
        throttle.record_failure(client)
        audit.record("admin.login_failure", client=client)
        return render(request, "login.html", error="error.bad_password")

    @app.post("/logout")
    async def logout(request: Request, _csrf: None = Depends(sec.verify_csrf)) -> RedirectResponse:
        sec.logout_session(request)
        return RedirectResponse("/login", status_code=303)

    @app.get("/password", response_class=HTMLResponse)
    async def password_form(request: Request, _: None = Depends(sec.require_login)) -> HTMLResponse:
        return render(request, "password.html")

    @app.post("/password")
    async def password_submit(
        request: Request,
        current: str = Form(...),
        password: str = Form(...),
        confirm: str = Form(...),
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> HTMLResponse:
        if not db.verify_admin_password(current):
            return render(request, "password.html", error="error.bad_password")
        if len(password) < 8 or password != confirm:
            return render(request, "password.html", error="error.password_mismatch")
        db.set_admin_password(password)
        audit.record("admin.password_changed")
        return render(request, "password.html", ok=True)

    # ------------------------------------------------------------------ #
    # Dashboard + service CRUD
    # ------------------------------------------------------------------ #

    @app.get("/", response_class=HTMLResponse, response_model=None)
    async def dashboard(request: Request) -> Response:
        if not db.is_admin_password_set():
            return RedirectResponse("/setup", status_code=303)
        if not sec.is_authenticated(request):
            return RedirectResponse("/login", status_code=303)
        return render(
            request,
            "dashboard.html",
            services=db.list_services(),
            loaded=set(manager.loaded_instance_ids()),
        )

    @app.get("/services/new", response_class=HTMLResponse)
    async def new_service_form(
        request: Request, _: None = Depends(sec.require_login)
    ) -> HTMLResponse:
        return render(
            request, "service_form.html", service=None, service_types=list_service_types()
        )

    @app.post("/services", response_model=None)
    async def create_service(
        request: Request,
        service_type: str = Form(...),
        slug: str = Form(...),
        label: str = Form(""),
        base_url: str = Form(...),
        api_key: str = Form(...),
        enabled: bool = Form(False),
        expose_destructive: bool = Form(False),
        default_quality_profile: str = Form(""),
        default_root_folder: str = Form(""),
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> HTMLResponse | RedirectResponse:
        try:
            row = db.create_service(
                service_type=service_type,
                slug=slug.strip(),
                label=label.strip() or slug.strip(),
                base_url=base_url.strip(),
                api_key=api_key,
                enabled=enabled,
                expose_destructive=expose_destructive,
                default_quality_profile=default_quality_profile.strip() or None,
                default_root_folder=default_root_folder.strip() or None,
            )
        except Exception as exc:  # noqa: BLE001 - show a friendly message
            logger.warning("Create service failed: %s", exc)
            return render(
                request,
                "service_form.html",
                service=None,
                service_types=list_service_types(),
                error="error.create_failed",
            )
        audit.record("service.created", slug=row.slug, service_type=row.service_type)
        await manager.reload_service(row.instance_id)
        return RedirectResponse("/", status_code=303)

    @app.get("/services/{instance_id}", response_class=HTMLResponse, response_model=None)
    async def edit_service_form(
        request: Request, instance_id: str, _: None = Depends(sec.require_login)
    ) -> HTMLResponse | RedirectResponse:
        row = db.get_service(instance_id)
        if row is None:
            return RedirectResponse("/", status_code=303)
        return render(
            request, "service_form.html", service=row, service_types=list_service_types()
        )

    @app.post("/services/{instance_id}")
    async def update_service(
        request: Request,
        instance_id: str,
        label: str = Form(""),
        base_url: str = Form(...),
        api_key: str = Form(""),
        enabled: bool = Form(False),
        expose_destructive: bool = Form(False),
        default_quality_profile: str = Form(""),
        default_root_folder: str = Form(""),
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> RedirectResponse:
        db.update_service(
            instance_id,
            api_key=api_key or None,
            label=label.strip(),
            base_url=base_url.strip(),
            enabled=enabled,
            expose_destructive=expose_destructive,
            default_quality_profile=default_quality_profile.strip() or None,
            default_root_folder=default_root_folder.strip() or None,
        )
        audit.record("service.updated", instance_id=instance_id)
        await manager.reload_service(instance_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/services/{instance_id}/toggle")
    async def toggle_service(
        request: Request,
        instance_id: str,
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> RedirectResponse:
        row = db.get_service(instance_id)
        if row is not None:
            db.update_service(instance_id, enabled=not row.enabled)
            audit.record("service.toggled", instance_id=instance_id, enabled=not row.enabled)
            await manager.reload_service(instance_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/services/{instance_id}/delete")
    async def delete_service(
        request: Request,
        instance_id: str,
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> RedirectResponse:
        db.delete_service(instance_id)
        audit.record("service.deleted", instance_id=instance_id)
        await manager.reload_service(instance_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/services/{instance_id}/test", response_class=HTMLResponse, response_model=None)
    async def test_service(
        request: Request,
        instance_id: str,
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> HTMLResponse | RedirectResponse:
        result = await manager.test_instance(instance_id)
        audit.record("service.tested", instance_id=instance_id, ok=result.ok)
        return render(
            request,
            "dashboard.html",
            services=db.list_services(),
            loaded=set(manager.loaded_instance_ids()),
            test_result={
                "instance_id": instance_id,
                "ok": result.ok,
                "code": result.code,
                "version": result.version,
            },
        )

    # ------------------------------------------------------------------ #
    # Connect page (MCP endpoint + token + per-client snippets)
    # ------------------------------------------------------------------ #

    @app.get("/connect", response_class=HTMLResponse)
    async def connect_page(request: Request, _: None = Depends(sec.require_login)) -> HTMLResponse:
        host = (request.headers.get("host") or "localhost").split(":")[0]
        scheme = "http"
        mcp_url = f"{scheme}://{host}:{settings.mcp_port}/mcp"
        return render(
            request,
            "connect.html",
            mcp_url=mcp_url,
            token=settings.get_token(),
            token_from_env=settings.token_from_env,
        )

    @app.post("/connect/rotate")
    async def rotate_token(
        request: Request,
        _: None = Depends(sec.require_login),
        _csrf: None = Depends(sec.verify_csrf),
    ) -> RedirectResponse:
        if not settings.token_from_env:
            settings.rotate_token()
            audit.record("mcp.token_rotated")
        return RedirectResponse("/connect", status_code=303)

    return app
