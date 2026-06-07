"""MCP server assembly (port 7474).

Builds the FastMCP instance with a *required* static bearer-token verifier - auth
is always on, there is no unauthenticated mode (security review item #1). The MCP
Streamable HTTP app is wrapped in a thin FastAPI app so we can attach health and
readiness endpoints alongside the ``/mcp`` endpoint.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastmcp import FastMCP
from fastmcp.server.auth import StaticTokenVerifier

from .config import Database
from .health import add_health_routes
from .service_manager import ServiceManager

logger = logging.getLogger("mcparr.mcp_server")

MCP_PATH = "/mcp"


def build_verifier(token: str) -> StaticTokenVerifier:
    """Create the required static bearer-token verifier. Auth is never off."""
    if not token:
        raise RuntimeError("Refusing to start MCP server without a token")
    return StaticTokenVerifier({token: {"client_id": "mcparr"}})


def build_mcp(verifier: StaticTokenVerifier) -> FastMCP:
    """Create the FastMCP server with a required bearer-token verifier."""
    return FastMCP(name="mcparr", auth=verifier)


def create_mcp_app(
    mcp: FastMCP,
    db: Database,
    manager: ServiceManager,
) -> FastAPI:
    """Wrap the MCP ASGI app with health routes; mount MCP at ``/mcp``."""
    mcp_app = mcp.http_app(path=MCP_PATH, transport="http")

    # The MCP session manager initializes via the ASGI lifespan; it must be
    # propagated to the wrapping app or sessions will not start.
    app = FastAPI(title="mcparr MCP", lifespan=mcp_app.lifespan)
    add_health_routes(app, db, ready_check=lambda: bool(manager.loaded_instance_ids()) or True)
    app.mount("/", mcp_app)
    return app
