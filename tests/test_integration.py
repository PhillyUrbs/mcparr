"""Integration tests for ServiceManager <-> FastMCP tool registration."""

from __future__ import annotations

import pytest

from mcparr.config import Database
from mcparr.logging import AuditLog
from mcparr.mcp_server import build_mcp, build_verifier
from mcparr.service_manager import ServiceManager


async def _tool_names(mcp) -> set[str]:
    tools = await mcp.list_tools()
    return {t.name for t in tools}


@pytest.fixture
def manager(db: Database, audit: AuditLog):
    mcp = build_mcp(build_verifier("test-token"))
    return ServiceManager(db, mcp, audit)


async def test_register_enabled_adds_tools(db: Database, manager: ServiceManager):
    db.create_service(
        service_type="radarr", slug="radarr", label="", base_url="http://radarr.test",
        api_key="k", enabled=True,
    )
    await manager.register_enabled()
    names = await _tool_names(manager.mcp)
    assert "radarr_search_movies" in names
    assert "radarr_list_quality_profiles" in names


async def test_disable_removes_tools(db: Database, manager: ServiceManager):
    row = db.create_service(
        service_type="radarr", slug="radarr", label="", base_url="http://radarr.test",
        api_key="k", enabled=True,
    )
    await manager.register_enabled()
    assert "radarr_search_movies" in await _tool_names(manager.mcp)

    db.update_service(row.instance_id, enabled=False)
    await manager.reload_service(row.instance_id)
    names = await _tool_names(manager.mcp)
    assert not any(n.startswith("radarr_") for n in names)
    assert manager.loaded_instance_ids() == []


async def test_multi_instance_distinct_slugs(db: Database, manager: ServiceManager):
    db.create_service(
        service_type="radarr", slug="radarr", label="HD", base_url="http://hd.test",
        api_key="k", enabled=True,
    )
    db.create_service(
        service_type="radarr", slug="radarr4k", label="4K", base_url="http://4k.test",
        api_key="k", enabled=True,
    )
    await manager.register_enabled()
    names = await _tool_names(manager.mcp)
    assert "radarr_search_movies" in names
    assert "radarr4k_search_movies" in names


async def test_destructive_tool_gated_at_manager(db: Database, manager: ServiceManager):
    row = db.create_service(
        service_type="radarr", slug="radarr", label="", base_url="http://radarr.test",
        api_key="k", enabled=True, expose_destructive=False,
    )
    await manager.register_enabled()
    assert "radarr_delete_movie" not in await _tool_names(manager.mcp)

    db.update_service(row.instance_id, expose_destructive=True)
    await manager.reload_service(row.instance_id)
    assert "radarr_delete_movie" in await _tool_names(manager.mcp)


async def test_aclose_all_clears(db: Database, manager: ServiceManager):
    db.create_service(
        service_type="sonarr", slug="sonarr", label="", base_url="http://sonarr.test",
        api_key="k", enabled=True,
    )
    await manager.register_enabled()
    assert manager.loaded_instance_ids()
    await manager.aclose_all()
    assert manager.loaded_instance_ids() == []
