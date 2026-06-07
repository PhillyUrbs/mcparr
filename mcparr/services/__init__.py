"""Service module package.

Each supported service (Radarr, Sonarr, ...) is a :class:`ServiceModule`
subclass registered with :func:`register`. The ServiceManager discovers modules
through the registry and instantiates one per enabled config instance.
"""

from __future__ import annotations

# Import side-effect modules so their @register decorators run.
from . import radarr, sonarr  # noqa: E402,F401  (registration side effects)
from .base import (
    ConnectionResult,
    ServiceModule,
    ToolSpec,
    get_module_class,
    list_service_types,
    register,
)

__all__ = [
    "ConnectionResult",
    "ServiceModule",
    "ToolSpec",
    "get_module_class",
    "list_service_types",
    "register",
]
