"""Shared health/readiness endpoints for both ports.

Liveness (``/health``) answers "is the process up?"; readiness (``/ready``)
answers "can it actually serve?" by checking the database and that MCP tool
registration has completed (plan decision #6).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import text

from . import __version__
from .config import Database


def add_health_routes(
    app: FastAPI,
    db: Database,
    *,
    ready_check: Callable[[], bool] | None = None,
) -> None:
    """Attach ``/health`` and ``/ready`` to a FastAPI app."""

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        return JSONResponse({"status": "ok", "version": __version__})

    @app.get("/ready", include_in_schema=False)
    async def ready() -> JSONResponse:
        checks: dict[str, bool] = {}
        try:
            with db.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            checks["database"] = True
        except Exception:  # noqa: BLE001 - readiness must never raise
            checks["database"] = False
        checks["mcp"] = ready_check() if ready_check else True

        ok = all(checks.values())
        return JSONResponse(
            {"status": "ready" if ok else "not_ready", "checks": checks},
            status_code=200 if ok else 503,
        )
