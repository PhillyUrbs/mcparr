"""Service module tests using respx to mock the *arr HTTP APIs."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcparr.services.radarr import RadarrModule
from mcparr.services.sonarr import SonarrModule

from .conftest import make_instance


def _find(tools, name: str):
    for spec in tools:
        if spec.name == name:
            return spec
    raise AssertionError(f"tool {name} not found in {[t.name for t in tools]}")


@respx.mock
async def test_radarr_test_connection_ok():
    respx.get("http://radarr.test/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "5.1.0"})
    )
    module = RadarrModule(make_instance())
    try:
        result = await module.test_connection()
    finally:
        await module.aclose()
    assert result.ok
    assert result.version == "5.1.0"
    assert result.code == "ok.connected"


@respx.mock
async def test_radarr_test_connection_unreachable():
    respx.get("http://radarr.test/api/v3/system/status").mock(
        side_effect=httpx.ConnectError("nope")
    )
    module = RadarrModule(make_instance())
    try:
        result = await module.test_connection()
    finally:
        await module.aclose()
    assert not result.ok
    assert result.code == "error.service_unreachable"


@respx.mock
async def test_radarr_search_movies_projects_fields():
    respx.get("http://radarr.test/api/v3/movie/lookup").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": 1, "title": "Dune", "year": 2021, "tmdbId": 438631, "overview": "x"},
                {"id": 2, "title": "Arrival", "year": 2016, "tmdbId": 329865, "overview": "y"},
            ],
        )
    )
    module = RadarrModule(make_instance())
    try:
        tools = module.get_tools()
        search = _find(tools, "radarr_search_movies")
        results = await search.handler(term="dune", limit=5)
    finally:
        await module.aclose()
    assert results[0]["title"] == "Dune"
    # Projected: noisy fields like "overview" are dropped.
    assert "overview" not in results[0]


@respx.mock
async def test_radarr_add_movie_resolves_names():
    respx.get("http://radarr.test/api/v3/movie/lookup/tmdb").mock(
        return_value=httpx.Response(200, json={"title": "Dune", "tmdbId": 438631})
    )
    respx.get("http://radarr.test/api/v3/qualityprofile").mock(
        return_value=httpx.Response(200, json=[{"id": 7, "name": "HD-1080p"}])
    )
    respx.get("http://radarr.test/api/v3/rootfolder").mock(
        return_value=httpx.Response(200, json=[{"id": 1, "path": "/movies"}])
    )
    posted = {}

    def _capture(request: httpx.Request) -> httpx.Response:
        import json

        posted.update(json.loads(request.content))
        return httpx.Response(201, json={"id": 99})

    respx.post("http://radarr.test/api/v3/movie").mock(side_effect=_capture)

    module = RadarrModule(make_instance())
    try:
        add = _find(module.get_tools(), "radarr_add_movie")
        await add.handler(tmdb_id=438631)
    finally:
        await module.aclose()
    assert posted["qualityProfileId"] == 7
    assert posted["rootFolderPath"] == "/movies"


def test_destructive_tool_gating():
    safe = RadarrModule(make_instance(expose_destructive=False))
    names = {t.name for t in safe.get_tools()}
    assert "radarr_delete_movie" not in names

    risky = RadarrModule(make_instance(expose_destructive=True))
    risky_tools = {t.name: t for t in risky.get_tools()}
    assert "radarr_delete_movie" in risky_tools
    assert risky_tools["radarr_delete_movie"].destructive


@respx.mock
async def test_sonarr_test_connection_ok():
    respx.get("http://sonarr.test/api/v3/system/status").mock(
        return_value=httpx.Response(200, json={"version": "4.0.0"})
    )
    module = SonarrModule(make_instance(slug="sonarr", service_type="sonarr",
                                        base_url="http://sonarr.test"))
    try:
        result = await module.test_connection()
    finally:
        await module.aclose()
    assert result.ok
    assert result.version == "4.0.0"


def test_invalid_base_url_rejected():
    from mcparr.errors import ValidationError

    bad = make_instance(base_url="ftp://radarr.test")
    with pytest.raises(ValidationError):
        RadarrModule(bad)
