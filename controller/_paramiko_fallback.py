"""Minimal ``paramiko`` fallback used in unit tests.

The production code relies on :mod:`paramiko` for SSH interactions.  The unit
test environment, however, does not install optional dependencies to keep the
runtime lightweight.  Importing :mod:`controller.app` should therefore not
explode when :mod:`paramiko` is missing.  This module provides a tiny shim that
exposes the attributes used by the application and raises a descriptive error
if any SSH functionality is exercised.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

__all__ = ["paramiko"]

_ERROR_MESSAGE = (
    "paramiko is required for SSH features. Install optional dependency 'paramiko'"
    " to enable SSH connectivity."
)


class _UnavailableKey:
    """Placeholder for Paramiko private key loaders."""

    @classmethod
    def from_private_key(cls, *args: Any, **kwargs: Any) -> "_UnavailableKey":
        raise ModuleNotFoundError(_ERROR_MESSAGE)

    @classmethod
    def from_private_key_file(cls, *args: Any, **kwargs: Any) -> "_UnavailableKey":
        raise ModuleNotFoundError(_ERROR_MESSAGE)


class _UnavailablePolicy:
    """Placeholder for host key policy classes."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - trivial
        raise ModuleNotFoundError(_ERROR_MESSAGE)


class _UnavailableSSHClient:
    """Placeholder for :class:`paramiko.SSHClient`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover - trivial
        raise ModuleNotFoundError(_ERROR_MESSAGE)


class _NoValidConnectionsError(RuntimeError):
    """Fallback for :class:`paramiko.ssh_exception.NoValidConnectionsError`."""


paramiko = SimpleNamespace(
    PKey=_UnavailableKey,
    Ed25519Key=_UnavailableKey,
    RSAKey=_UnavailableKey,
    ECDSAKey=_UnavailableKey,
    SSHClient=_UnavailableSSHClient,
    AutoAddPolicy=_UnavailablePolicy,
    RejectPolicy=_UnavailablePolicy,
    ssh_exception=SimpleNamespace(NoValidConnectionsError=_NoValidConnectionsError),
)

