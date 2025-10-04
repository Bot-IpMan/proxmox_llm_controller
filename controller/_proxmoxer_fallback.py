"""Fallback implementations for the tiny subset of ``proxmoxer`` used in tests."""
from __future__ import annotations

from typing import Any, Optional

__all__ = ["ProxmoxAPI", "ResourceException"]


class ProxmoxAPI:
    """Minimal stand-in for :class:`proxmoxer.ProxmoxAPI`.

    The real client exposes a fluent interface for traversing the Proxmox REST
    API.  The unit tests patch the controller with bespoke doubles instead of
    performing live API calls, so the fallback only needs to support
    instantiation.  Accessing any attribute will raise an informative error to
    signal missing test configuration.
    """

    def __init__(self, host: str, **kwargs: Any) -> None:
        self.host = host
        self._connection_params = dict(kwargs)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        params = ", ".join(f"{k}={v!r}" for k, v in self._connection_params.items())
        return f"<ProxmoxAPI host={self.host!r}{', ' if params else ''}{params}>"

    def __getattr__(self, name: str) -> Any:
        raise RuntimeError(
            "The lightweight ProxmoxAPI fallback does not implement attribute "
            "access. Tests should patch controller.get_proxmox() to return a "
            "test double that mimics the required behaviour."
        )


class ResourceException(Exception):
    """Simplified version of :class:`proxmoxer.core.ResourceException`."""

    status_code: Optional[int]
    status_message: Optional[str]
    message: Optional[str]
    errors: Optional[Any]

    def __init__(
        self,
        status_code: Optional[int],
        status_message: Optional[str] = None,
        message: Optional[str] = None,
        errors: Optional[Any] = None,
    ) -> None:
        self.status_code = status_code
        self.status_message = status_message
        self.message = message
        self.errors = errors
        super().__init__(self._build_message())

    def _build_message(self) -> str:
        main = ""
        if self.status_code is not None:
            main = str(self.status_code)
        if self.status_message:
            main = f"{main} {self.status_message}".strip()
        if self.message:
            if main:
                main = f"{main}: {self.message}"
            else:
                main = self.message
        if not main:
            main = "Proxmox resource error"

        details = self._format_errors(self.errors)
        if details:
            return f"{main} {details}".rstrip()
        return main

    @staticmethod
    def _format_errors(errors: Optional[Any]) -> str:
        if errors is None:
            return ""
        if isinstance(errors, dict):
            parts = [f"{key}: {value}" for key, value in errors.items()]
            return " ; ".join(parts)
        if isinstance(errors, (list, tuple, set)):
            parts = [str(item) for item in errors]
            return " ; ".join(parts)
        return str(errors)

    def __str__(self) -> str:  # pragma: no cover - delegated to _build_message
        return self._build_message()
