"""Application entry point: wires up both servers in a single process.

Two uvicorn servers (the config UI on 7475 and the MCP endpoint on 7474) run on
one asyncio event loop via ``asyncio.gather``. They share the database, the
encryption key, and the live ServiceManager, which keeps hot tool reloads simple.
On SIGINT/SIGTERM both servers stop, every service HTTP client is closed, and the
database engine is disposed.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from typing import Any

import uvicorn
from fastmcp.server.auth import StaticTokenVerifier

from .config import (
    ENV_ADMIN_PASSWORD,
    ENV_MCP_TOKEN,
    AppPaths,
    Database,
    Secrets,
    load_or_create_fernet,
    load_or_create_mcp_token,
    load_or_create_session_secret,
    maybe_seed,
    rotate_mcp_token,
)
from .logging import AuditLog, configure_logging
from .mcp_server import build_mcp, build_verifier, create_mcp_app
from .service_manager import ServiceManager
from .ui import create_ui_app
from .ui.app import UISettings

logger = logging.getLogger("mcparr.main")

DEFAULT_UI_PORT = 7475
DEFAULT_MCP_PORT = 7474
DEFAULT_HOST = "127.0.0.1"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    return int(raw) if raw and raw.isdigit() else default


class _TokenHolder:
    """Holds the current MCP token and keeps the live verifier in sync."""

    def __init__(self, token: str, verifier: StaticTokenVerifier, paths: AppPaths) -> None:
        self._token = token
        self._verifier = verifier
        self._paths = paths

    @property
    def value(self) -> str:
        return self._token

    def rotate(self) -> str:
        self._token = rotate_mcp_token(self._paths)
        # verify_token reads .tokens at call time, so this takes effect at once
        # and invalidates the previous token.
        self._verifier.tokens = {self._token: {"client_id": "mcparr"}}
        return self._token


async def _serve(app: Any, host: str, port: int) -> uvicorn.Server:
    config = uvicorn.Config(app, host=host, port=port, log_config=None, lifespan="on")
    return uvicorn.Server(config)


async def main_async() -> None:
    configure_logging(os.environ.get("MCPARR_LOG_LEVEL", "INFO"))

    paths = AppPaths.resolve()
    fernet = load_or_create_fernet(paths)
    secrets_ = Secrets(fernet)
    db = Database.open(paths, secrets_)
    audit = AuditLog(paths.audit_log_path)

    # Allow a headless first-run admin password via env.
    env_password = os.environ.get(ENV_ADMIN_PASSWORD)
    if env_password and not db.is_admin_password_set():
        db.set_admin_password(env_password)
        audit.record("admin.password_set", source="env")

    seed = maybe_seed(db, paths)
    if seed.created:
        logger.info("Seeded services: %s", ", ".join(seed.created))

    token = load_or_create_mcp_token(paths)
    token_from_env = bool(os.environ.get(ENV_MCP_TOKEN))

    verifier = build_verifier(token)
    mcp = build_mcp(verifier)
    holder = _TokenHolder(token, verifier, paths)

    manager = ServiceManager(db, mcp, audit)
    await manager.register_enabled()
    logger.info(
        "MCP ready with %d service instance(s). Token %s.",
        len(manager.loaded_instance_ids()),
        "from env" if token_from_env else "available in the UI Connect page",
    )

    session_secret = load_or_create_session_secret(paths)
    ui_port = _env_int("MCPARR_UI_PORT", DEFAULT_UI_PORT)
    mcp_port = _env_int("MCPARR_MCP_PORT", DEFAULT_MCP_PORT)
    host = os.environ.get("MCPARR_HOST", DEFAULT_HOST)

    ui_settings = UISettings(
        mcp_port=mcp_port,
        get_token=lambda: holder.value,
        rotate_token=holder.rotate,
        token_from_env=token_from_env,
    )
    ui_app = create_ui_app(
        db=db,
        manager=manager,
        audit=audit,
        session_secret=session_secret,
        settings=ui_settings,
    )
    mcp_app = create_mcp_app(mcp, db, manager)

    ui_server = await _serve(ui_app, host, ui_port)
    mcp_server = await _serve(mcp_app, host, mcp_port)

    stop = asyncio.Event()

    def _request_stop(*_: object) -> None:
        stop.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            # Windows: fall back to default KeyboardInterrupt handling.
            loop.add_signal_handler(sig, _request_stop)

    async def _run(server: uvicorn.Server) -> None:
        await server.serve()

    async def _watch_stop() -> None:
        await stop.wait()
        ui_server.should_exit = True
        mcp_server.should_exit = True

    logger.info("UI on http://%s:%d  |  MCP on http://%s:%d/mcp", host, ui_port, host, mcp_port)
    try:
        await asyncio.gather(_run(ui_server), _run(mcp_server), _watch_stop())
    finally:
        await manager.aclose_all()
        db.dispose()
        logger.info("Shutdown complete.")


def run() -> None:
    """Synchronous console-script entry point."""
    with contextlib.suppress(KeyboardInterrupt):
        asyncio.run(main_async())


if __name__ == "__main__":
    run()
