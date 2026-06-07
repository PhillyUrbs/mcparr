"""Shared error taxonomy for mcparr.

Every error carries a machine-readable ``code``. The codes double as i18n keys:
service modules and core logic raise these, the UI translates the code at render
time, and the audit log records the code (never the raw, potentially sensitive
message). This keeps English copy out of the service layer (see plan decision #7
on i18n and #11 on the error taxonomy).
"""

from __future__ import annotations


class McparrError(Exception):
    """Base class for all mcparr errors.

    Args:
        code: A stable machine-readable identifier (also used as an i18n key).
        message: A developer-facing English message for logs. Not shown verbatim
            to end users in localized contexts.
        params: Optional values to interpolate into a translated message.
    """

    code: str = "error.unknown"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        params: dict[str, object] | None = None,
    ) -> None:
        self.code = code or self.code
        self.params = params or {}
        super().__init__(message or self.code)


class ConfigError(McparrError):
    """Configuration is missing, malformed, or inconsistent."""

    code = "error.config"


class ServiceUnreachable(McparrError):
    """A downstream service could not be reached (network/timeout)."""

    code = "error.service_unreachable"


class AuthFailed(McparrError):
    """Authentication against a downstream service failed (bad API key)."""

    code = "error.auth_failed"


class UpstreamError(McparrError):
    """A downstream service returned an unexpected error response."""

    code = "error.upstream"


class NotFound(McparrError):
    """A requested resource does not exist on the downstream service."""

    code = "error.not_found"


class ValidationError(McparrError):
    """User-supplied input failed validation before reaching a service."""

    code = "error.validation"
