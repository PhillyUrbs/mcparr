"""Sonarr service module (TV series).

Targets the Sonarr v3 API, mirroring the Radarr module's structure. Read-only
tools are always available; the add tool is a non-destructive write; the delete
tool is destructive and gated behind ``expose_destructive``.
"""

from __future__ import annotations

from typing import Any

from ._arr import ArrServiceModule
from .base import ToolSpec, clamp_limit, project, register

_SERIES_FIELDS = (
    "id",
    "title",
    "year",
    "tvdbId",
    "status",
    "monitored",
    "seasonCount",
    "sizeOnDisk",
)


@register
class SonarrModule(ArrServiceModule):
    service_type = "sonarr"
    display_name = "Sonarr"
    default_port = 8989
    icon = "sonarr"
    doc_url = "https://sonarr.tv"

    def get_tools(self) -> list[ToolSpec]:
        tools = self._discovery_tools()
        tools.extend(self._series_tools())
        if self.config.expose_destructive:
            tools.append(self._delete_tool())
        return tools

    def _series_tools(self) -> list[ToolSpec]:
        async def search_series(term: str, limit: int = 25) -> list[dict[str, Any]]:
            """Search for TV series by title to add to Sonarr.

            Args:
                term: Series title or search text.
                limit: Maximum number of results to return (1-100).
            """
            results = await self.request(
                "GET", f"{self.api_base}/series/lookup", params={"term": term}
            )
            return project(results or [], _SERIES_FIELDS)[: clamp_limit(limit)]

        async def list_series(limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
            """List TV series in the Sonarr library (compact fields).

            Args:
                limit: Maximum number of series to return (1-100).
                offset: Number of series to skip, for paging.
            """
            series = await self.request("GET", f"{self.api_base}/series")
            window = (series or [])[max(0, offset) : max(0, offset) + clamp_limit(limit)]
            return project(window, _SERIES_FIELDS)

        async def get_series(series_id: int) -> dict[str, Any]:
            """Get the full record for a single series by its Sonarr id.

            Args:
                series_id: The Sonarr series id (not the TVDB id).
            """
            return await self.request("GET", f"{self.api_base}/series/{series_id}")

        async def add_series(
            tvdb_id: int,
            quality_profile: str | None = None,
            root_folder: str | None = None,
            monitored: bool = True,
            search_now: bool = True,
        ) -> dict[str, Any]:
            """Add a TV series to Sonarr by its TVDB id.

            Args:
                tvdb_id: TheTVDB id of the series.
                quality_profile: Quality profile name; falls back to the instance default.
                root_folder: Root folder path or name; falls back to the instance default.
                monitored: Whether Sonarr should monitor the series.
                search_now: Whether to search for episodes immediately after adding.
            """
            lookup = await self.request(
                "GET", f"{self.api_base}/series/lookup", params={"term": f"tvdb:{tvdb_id}"}
            )
            record = (lookup or [{}])[0] if isinstance(lookup, list) else (lookup or {})
            profile_id = await self.resolve_quality_profile_id(quality_profile)
            folder_path = await self.resolve_root_folder_path(root_folder)
            payload = dict(record)
            payload.update(
                {
                    "qualityProfileId": profile_id,
                    "rootFolderPath": folder_path,
                    "monitored": monitored,
                    "addOptions": {"searchForMissingEpisodes": search_now},
                }
            )
            return await self.request("POST", f"{self.api_base}/series", json=payload)

        return [
            ToolSpec(name=f"{self.slug}_search_series", handler=search_series, read_only=True),
            ToolSpec(name=f"{self.slug}_list_series", handler=list_series, read_only=True),
            ToolSpec(name=f"{self.slug}_get_series", handler=get_series, read_only=True),
            ToolSpec(name=f"{self.slug}_add_series", handler=add_series, read_only=False),
        ]

    def _delete_tool(self) -> ToolSpec:
        async def delete_series(series_id: int, delete_files: bool = False) -> dict[str, Any]:
            """Permanently remove a series from Sonarr.

            Args:
                series_id: The Sonarr series id to delete.
                delete_files: Whether to also delete the series' files from disk.
            """
            await self.request(
                "DELETE",
                f"{self.api_base}/series/{series_id}",
                params={"deleteFiles": str(delete_files).lower()},
            )
            return {"deleted": series_id}

        return ToolSpec(
            name=f"{self.slug}_delete_series",
            handler=delete_series,
            read_only=False,
            destructive=True,
        )
