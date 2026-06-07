"""Shared base for Radarr/Sonarr-style services (the *arr v3 API shape).

Radarr and Sonarr expose nearly identical plumbing: a ``system/status`` probe,
``qualityprofile`` and ``rootfolder`` discovery endpoints, and name-based
resolution for add operations. That common ground lives here so each concrete
service only declares its entity-specific tools.
"""

from __future__ import annotations

from typing import Any

from ..errors import ValidationError
from .base import ConnectionResult, ServiceModule, ToolSpec


class ArrServiceModule(ServiceModule):
    """Common behaviour for *arr v3 services."""

    api_base: str = "/api/v3"

    async def test_connection(self) -> ConnectionResult:
        try:
            status = await self.request("GET", f"{self.api_base}/system/status")
        except Exception:  # noqa: BLE001 - mapped to a machine code by caller
            return ConnectionResult(ok=False, code="error.service_unreachable")
        version = None
        if isinstance(status, dict):
            version = status.get("version")
        return ConnectionResult(ok=True, code="ok.connected", version=version)

    # -- discovery + name resolution ------------------------------------- #

    async def list_quality_profiles(self) -> list[dict[str, Any]]:
        profiles = await self.request("GET", f"{self.api_base}/qualityprofile")
        return profiles or []

    async def list_root_folders(self) -> list[dict[str, Any]]:
        folders = await self.request("GET", f"{self.api_base}/rootfolder")
        return folders or []

    async def resolve_quality_profile_id(self, name: str | None) -> int:
        target = name or self.config.default_quality_profile
        profiles = await self.list_quality_profiles()
        if not profiles:
            raise ValidationError(
                "No quality profiles are configured", code="error.no_quality_profile"
            )
        if target:
            for profile in profiles:
                if str(profile.get("name", "")).lower() == target.lower():
                    return int(profile["id"])
            raise ValidationError(
                f"Unknown quality profile '{target}'", code="error.unknown_quality_profile"
            )
        # No name and no default: fall back to the first profile.
        return int(profiles[0]["id"])

    async def resolve_root_folder_path(self, name: str | None) -> str:
        target = name or self.config.default_root_folder
        folders = await self.list_root_folders()
        if not folders:
            raise ValidationError(
                "No root folders are configured", code="error.no_root_folder"
            )
        if target:
            for folder in folders:
                path = str(folder.get("path", ""))
                if path == target or path.rstrip("/").endswith(target.rstrip("/")):
                    return path
            raise ValidationError(
                f"Unknown root folder '{target}'", code="error.unknown_root_folder"
            )
        return str(folders[0]["path"])

    # -- shared discovery tools ------------------------------------------ #

    def _discovery_tools(self) -> list[ToolSpec]:
        async def list_quality_profiles() -> list[dict[str, Any]]:
            """List available quality profiles (name and id) for this instance."""
            profiles = await self.list_quality_profiles()
            return [{"id": p.get("id"), "name": p.get("name")} for p in profiles]

        async def list_root_folders() -> list[dict[str, Any]]:
            """List configured root folders (path and free space) for this instance."""
            folders = await self.list_root_folders()
            return [
                {"path": f.get("path"), "freeSpace": f.get("freeSpace")} for f in folders
            ]

        return [
            ToolSpec(
                name=f"{self.slug}_list_quality_profiles",
                handler=list_quality_profiles,
                read_only=True,
            ),
            ToolSpec(
                name=f"{self.slug}_list_root_folders",
                handler=list_root_folders,
                read_only=True,
            ),
        ]
