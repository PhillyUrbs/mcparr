"""Base class, registry, and shared helpers for service modules.

A service module turns one configured instance into a set of MCP tools. The
base class owns the cross-cutting concerns so individual services stay small:

* a bounded, authenticated :mod:`httpx` client with a per-instance concurrency
  cap (plan decision #8);
* uniform error mapping onto the :mod:`mcparr.errors` taxonomy (#11);
* pagination clamping (#4) and result-field projection (#5) helpers;
* an SSRF guard for the user-supplied ``base_url`` (security review item #4).

Service modules deliberately do not import ``fastmcp`` - they return plain
:class:`ToolSpec` objects, which keeps them trivially unit-testable. The
ServiceManager converts ToolSpecs into FastMCP tools.
"""

from __future__ import annotations

import ipaddress
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar
from urllib.parse import urlparse

import anyio
import httpx

from ..errors import (
    AuthFailed,
    NotFound,
    ServiceUnreachable,
    UpstreamError,
    ValidationError,
)

if TYPE_CHECKING:
    from ..config import InstanceConfig

# Tunables shared across all services.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100
MAX_CONCURRENCY = 5
HTTP_TIMEOUT = httpx.Timeout(10.0, connect=5.0)
HTTP_LIMITS = httpx.Limits(max_connections=10, max_keepalive_connections=5)

# Hosts/ranges that must never be targeted (cloud metadata, link-local).
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


@dataclass(frozen=True)
class ConnectionResult:
    """Outcome of a connection test.

    ``code`` is a machine-readable status (an i18n key), translated by the UI;
    it is never a baked-in English sentence.
    """

    ok: bool
    code: str
    version: str | None = None


@dataclass
class ToolSpec:
    """A single MCP tool contributed by a service instance.

    ``name`` is already slug-prefixed (for example ``radarr_search_movies``).
    ``handler`` is an ``async`` function with typed parameters and a docstring;
    FastMCP derives the input schema and description from it.
    """

    name: str
    handler: Callable[..., Awaitable[Any]]
    description: str | None = None
    read_only: bool = True
    destructive: bool = False


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #

_REGISTRY: dict[str, type[ServiceModule]] = {}


def register(cls: type[ServiceModule]) -> type[ServiceModule]:
    """Class decorator that registers a service module by its ``service_type``."""
    key = cls.service_type
    if not key:
        raise ValueError(f"{cls.__name__} must define a non-empty service_type")
    if key in _REGISTRY:
        raise ValueError(f"Duplicate service_type '{key}'")
    _REGISTRY[key] = cls
    return cls


def get_module_class(service_type: str) -> type[ServiceModule] | None:
    return _REGISTRY.get(service_type)


def list_service_types() -> list[str]:
    return sorted(_REGISTRY)


# --------------------------------------------------------------------------- #
# SSRF guard + pagination/projection helpers
# --------------------------------------------------------------------------- #


def validate_base_url(url: str) -> None:
    """Reject obviously dangerous service URLs before any request is made."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValidationError(
            "base_url must use http or https", code="error.invalid_url"
        )
    host = parsed.hostname or ""
    if host in _BLOCKED_HOSTS:
        raise ValidationError("base_url host is not allowed", code="error.invalid_url")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return  # hostname, not a literal IP - allowed (homelab DNS names)
    if ip.is_link_local or ip.is_multicast or ip.is_unspecified:
        raise ValidationError("base_url host is not allowed", code="error.invalid_url")


def clamp_limit(limit: int | None, *, default: int = DEFAULT_LIMIT) -> int:
    """Clamp a caller-supplied limit into ``[1, MAX_LIMIT]``."""
    if limit is None:
        return default
    return max(1, min(int(limit), MAX_LIMIT))


def project(items: Sequence[dict[str, Any]], fields: Sequence[str]) -> list[dict[str, Any]]:
    """Return a trimmed view of each item containing only ``fields`` that exist."""
    return [{f: item[f] for f in fields if f in item} for item in items]


# --------------------------------------------------------------------------- #
# Base class
# --------------------------------------------------------------------------- #


class ServiceModule(ABC):
    """Abstract base for a single configured service instance."""

    # Class-level metadata (override in subclasses).
    service_type: ClassVar[str] = ""
    display_name: ClassVar[str] = ""
    default_port: ClassVar[int | None] = None
    icon: ClassVar[str] = ""
    doc_url: ClassVar[str] = ""

    def __init__(self, config: InstanceConfig) -> None:
        validate_base_url(config.base_url)
        self.config = config
        self.slug = config.slug
        self._client: httpx.AsyncClient | None = None
        self._semaphore = anyio.Semaphore(MAX_CONCURRENCY)

    # -- HTTP plumbing ---------------------------------------------------- #

    def _auth_headers(self) -> dict[str, str]:
        """Default *arr auth: the ``X-Api-Key`` header. Override if different."""
        return {"X-Api-Key": self.config.api_key}

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.config.base_url,
                headers=self._auth_headers(),
                timeout=HTTP_TIMEOUT,
                limits=HTTP_LIMITS,
            )
        return self._client

    async def request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Perform an HTTP request and return parsed JSON, mapping errors.

        Concurrency is capped per instance so a runaway client cannot hammer the
        downstream service.
        """
        client = self._ensure_client()
        async with self._semaphore:
            try:
                response = await client.request(method, path, **kwargs)
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                raise ServiceUnreachable(str(exc)) from exc
            except httpx.TimeoutException as exc:
                raise ServiceUnreachable("Request timed out", code="error.timeout") from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(str(exc)) from exc

        if response.status_code in (401, 403):
            raise AuthFailed(f"Authentication failed ({response.status_code})")
        if response.status_code == 404:
            raise NotFound("Resource not found")
        if response.status_code >= 400:
            raise UpstreamError(
                f"Upstream returned {response.status_code}",
                params={"status": response.status_code},
            )
        if not response.content:
            return None
        return response.json()

    async def aclose(self) -> None:
        """Close the shared HTTP client. Safe to call multiple times."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # -- contract --------------------------------------------------------- #

    @abstractmethod
    async def test_connection(self) -> ConnectionResult:
        """Probe the service's status/health endpoint."""
        raise NotImplementedError

    @abstractmethod
    def get_tools(self) -> list[ToolSpec]:
        """Return the tools this instance contributes.

        Implementations should omit destructive tools unless
        ``self.config.expose_destructive`` is set.
        """
        raise NotImplementedError
