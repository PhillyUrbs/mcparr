"""The ServiceManager - the single owner of live MCP tool registration.

Both the web UI and the MCP server import the same ServiceManager instance. It
holds the live :class:`FastMCP` server, the set of instantiated service modules
(keyed by instance id), and an :class:`asyncio.Lock` that serializes reloads so a
configuration change can never race a ``tools/list`` call (plan decisions #1, #5).

When a service instance is toggled or edited in the UI, the manager adds or
removes that instance's tools in place; FastMCP emits ``tools/list_changed`` so
connected clients refresh without reconnecting. Disabling an instance removes its
tools entirely; an enabled-but-unreachable instance keeps its tools registered
and surfaces a clean error at call time (decision #10).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from fastmcp import FastMCP
from fastmcp.tools import Tool
from mcp.types import ToolAnnotations

from .config import Database, InstanceConfig
from .errors import McparrError
from .logging import AuditLog
from .services.base import ConnectionResult, ServiceModule, ToolSpec, get_module_class

logger = logging.getLogger("mcparr.service_manager")


async def _safe_list(
    lister: Callable[[], Awaitable[list[dict[str, Any]]]] | None,
) -> list[dict[str, Any]]:
    """Call an optional discovery method, swallowing errors into an empty list."""
    if lister is None:
        return []
    try:
        return await lister()
    except Exception:  # noqa: BLE001 - discovery is best-effort for the UI
        return []



class ServiceManager:
    """Owns live tool registration for all configured service instances."""

    def __init__(self, db: Database, mcp: FastMCP, audit: AuditLog) -> None:
        self._db = db
        self._mcp = mcp
        self._audit = audit
        self._modules: dict[str, ServiceModule] = {}
        self._tool_names: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    @property
    def mcp(self) -> FastMCP:
        return self._mcp

    def loaded_instance_ids(self) -> list[str]:
        return list(self._modules)

    # -- startup ---------------------------------------------------------- #

    async def register_enabled(self) -> None:
        """Instantiate and register every enabled instance at startup."""
        async with self._lock:
            for cfg in self._db.load_enabled():
                await self._register_instance(cfg)

    # -- live reload ------------------------------------------------------ #

    async def reload_service(self, instance_id: str) -> None:
        """Re-sync a single instance's tools with its current DB state."""
        async with self._lock:
            await self._unregister_instance(instance_id)
            row = self._db.get_service(instance_id)
            if row is not None and row.enabled:
                await self._register_instance(self._db.to_instance_config(row))
        self._audit.record("service.reloaded", instance_id=instance_id)

    async def test_instance(self, instance_id: str) -> ConnectionResult:
        """Build a transient module for an instance and probe its connection.

        Works regardless of whether the instance is enabled/registered, so the UI
        can verify settings before turning a service on.
        """
        row = self._db.get_service(instance_id)
        if row is None:
            return ConnectionResult(ok=False, code="error.not_found")
        module_cls = get_module_class(row.service_type)
        if module_cls is None:
            return ConnectionResult(ok=False, code="error.unknown_service_type")
        module = module_cls(self._db.to_instance_config(row))
        try:
            return await module.test_connection()
        except Exception:  # noqa: BLE001 - surface a clean machine code to the UI
            return ConnectionResult(ok=False, code="error.service_unreachable")
        finally:
            await module.aclose()

    async def probe_connection(
        self, cfg: InstanceConfig
    ) -> tuple[ConnectionResult, list[dict], list[dict]]:
        """Test an arbitrary config and, on success, return its discovery lists.

        Used by the add/edit form to verify settings and populate the quality
        profile and root folder pickers before the instance is saved.
        """
        module_cls = get_module_class(cfg.service_type)
        if module_cls is None:
            return ConnectionResult(ok=False, code="error.unknown_service_type"), [], []
        try:
            module = module_cls(cfg)
        except McparrError as exc:
            return ConnectionResult(ok=False, code=exc.code), [], []
        except Exception:  # noqa: BLE001 - bad config must not crash the request
            return ConnectionResult(ok=False, code="error.config"), [], []
        try:
            result = await module.test_connection()
            if not result.ok:
                return result, [], []
            profiles = await _safe_list(getattr(module, "list_quality_profiles", None))
            folders = await _safe_list(getattr(module, "list_root_folders", None))
            return result, profiles, folders
        finally:
            await module.aclose()


    # -- internals -------------------------------------------------------- #

    async def _register_instance(self, cfg: InstanceConfig) -> None:
        module_cls = get_module_class(cfg.service_type)
        if module_cls is None:
            logger.warning("No service module for type '%s'", cfg.service_type)
            return
        try:
            module = module_cls(cfg)
        except Exception:  # noqa: BLE001 - bad config must not crash startup
            logger.exception("Failed to construct module for instance '%s'", cfg.slug)
            return

        names: list[str] = []
        for spec in module.get_tools():
            self._mcp.add_tool(self._build_tool(spec))
            names.append(spec.name)

        self._modules[cfg.instance_id] = module
        self._tool_names[cfg.instance_id] = names
        logger.info("Registered '%s' with %d tools", cfg.slug, len(names))

    async def _unregister_instance(self, instance_id: str) -> None:
        for name in self._tool_names.pop(instance_id, []):
            try:
                self._mcp.local_provider.remove_tool(name)
            except Exception:  # noqa: BLE001 - tool may already be gone
                logger.debug("Tool '%s' was not registered", name)
        module = self._modules.pop(instance_id, None)
        if module is not None:
            await module.aclose()

    def _build_tool(self, spec: ToolSpec) -> Tool:
        annotations = ToolAnnotations(
            readOnlyHint=spec.read_only,
            destructiveHint=spec.destructive,
        )
        return Tool.from_function(
            spec.handler,
            name=spec.name,
            description=spec.description,
            annotations=annotations,
        )

    # -- shutdown --------------------------------------------------------- #

    async def aclose_all(self) -> None:
        """Close every service module's HTTP client during shutdown."""
        async with self._lock:
            for module in self._modules.values():
                try:
                    await module.aclose()
                except Exception:  # noqa: BLE001 - best-effort teardown
                    logger.debug("Error closing module", exc_info=True)
            self._modules.clear()
            self._tool_names.clear()
