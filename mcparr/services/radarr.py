"""Radarr service module (movies).

Targets the Radarr v3 API. Read-only tools are always available; the add tool is
a write but non-destructive; the delete tool is destructive and only exposed when
the instance has ``expose_destructive`` enabled.
"""

from __future__ import annotations

from typing import Any

from ._arr import ArrServiceModule
from .base import ToolSpec, clamp_limit, project, register

# Compact projection for list/search results (full record via get_movie).
_MOVIE_FIELDS = (
    "id",
    "title",
    "year",
    "tmdbId",
    "status",
    "monitored",
    "hasFile",
    "sizeOnDisk",
)


@register
class RadarrModule(ArrServiceModule):
    service_type = "radarr"
    display_name = "Radarr"
    default_port = 7878
    icon = "radarr"
    doc_url = "https://radarr.video"

    def get_tools(self) -> list[ToolSpec]:
        tools = self._discovery_tools()
        tools.extend(self._movie_tools())
        if self.config.expose_destructive:
            tools.append(self._delete_tool())
        return tools

    def _movie_tools(self) -> list[ToolSpec]:
        async def search_movies(term: str, limit: int = 25) -> list[dict[str, Any]]:
            """Search for movies by title to add to Radarr.

            Args:
                term: Movie title or search text.
                limit: Maximum number of results to return (1-100).
            """
            results = await self.request(
                "GET", f"{self.api_base}/movie/lookup", params={"term": term}
            )
            return project(results or [], _MOVIE_FIELDS)[: clamp_limit(limit)]

        async def list_movies(limit: int = 25, offset: int = 0) -> list[dict[str, Any]]:
            """List movies in the Radarr library (compact fields).

            Args:
                limit: Maximum number of movies to return (1-100).
                offset: Number of movies to skip, for paging.
            """
            movies = await self.request("GET", f"{self.api_base}/movie")
            window = (movies or [])[max(0, offset) : max(0, offset) + clamp_limit(limit)]
            return project(window, _MOVIE_FIELDS)

        async def get_movie(movie_id: int) -> dict[str, Any]:
            """Get the full record for a single movie by its Radarr id.

            Args:
                movie_id: The Radarr movie id (not the TMDB id).
            """
            return await self.request("GET", f"{self.api_base}/movie/{movie_id}")

        async def add_movie(
            tmdb_id: int,
            quality_profile: str | None = None,
            root_folder: str | None = None,
            monitored: bool = True,
            search_now: bool = True,
        ) -> dict[str, Any]:
            """Add a movie to Radarr by its TMDB id.

            Args:
                tmdb_id: The Movie Database (TMDB) id of the movie.
                quality_profile: Quality profile name; falls back to the instance default.
                root_folder: Root folder path or name; falls back to the instance default.
                monitored: Whether Radarr should monitor the movie.
                search_now: Whether to search for a release immediately after adding.
            """
            lookup = await self.request(
                "GET", f"{self.api_base}/movie/lookup/tmdb", params={"tmdbId": tmdb_id}
            )
            profile_id = await self.resolve_quality_profile_id(quality_profile)
            folder_path = await self.resolve_root_folder_path(root_folder)
            payload = dict(lookup or {})
            payload.update(
                {
                    "qualityProfileId": profile_id,
                    "rootFolderPath": folder_path,
                    "monitored": monitored,
                    "addOptions": {"searchForMovie": search_now},
                }
            )
            return await self.request("POST", f"{self.api_base}/movie", json=payload)

        return [
            ToolSpec(name=f"{self.slug}_search_movies", handler=search_movies, read_only=True),
            ToolSpec(name=f"{self.slug}_list_movies", handler=list_movies, read_only=True),
            ToolSpec(name=f"{self.slug}_get_movie", handler=get_movie, read_only=True),
            ToolSpec(name=f"{self.slug}_add_movie", handler=add_movie, read_only=False),
        ]

    def _delete_tool(self) -> ToolSpec:
        async def delete_movie(movie_id: int, delete_files: bool = False) -> dict[str, Any]:
            """Permanently remove a movie from Radarr.

            Args:
                movie_id: The Radarr movie id to delete.
                delete_files: Whether to also delete the movie's files from disk.
            """
            await self.request(
                "DELETE",
                f"{self.api_base}/movie/{movie_id}",
                params={"deleteFiles": str(delete_files).lower()},
            )
            return {"deleted": movie_id}

        return ToolSpec(
            name=f"{self.slug}_delete_movie",
            handler=delete_movie,
            read_only=False,
            destructive=True,
        )
