"""Lightweight fallbacks for a subset of FastAPI features used in tests.

These classes and helpers provide just enough structure for the unit tests to
import :mod:`controller.app` when the real :mod:`fastapi` package is not
available (for example, in environments without network access).  The goal is
API compatibility rather than feature parity; only the small surface area
exercised by the tests is implemented here.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional

__all__ = [
    "CORSMiddleware",
    "FastAPI",
    "HTTPException",
    "Query",
]


class HTTPException(Exception):
    """Simple stand-in mirroring :class:`fastapi.HTTPException`."""

    def __init__(self, status_code: int, detail: Any) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def Query(default: Any, **_kwargs: Any) -> Any:
    """Return ``default`` while accepting FastAPI's declarative parameters."""

    return default


@dataclass
class CORSMiddleware:
    """No-op representation of FastAPI's CORS middleware."""

    app: Any
    allow_origins: Iterable[str]
    allow_credentials: bool = True
    allow_methods: Iterable[str] = ("*",)
    allow_headers: Iterable[str] = ("*",)


class FastAPI:
    """Minimal FastAPI-like application container."""

    def __init__(self, *, title: str = "FastAPI", version: str = "0.1.0") -> None:
        self.title = title
        self.version = version
        self._routes: List[Dict[str, Any]] = []
        self._middleware: List[CORSMiddleware] = []

    # ------------------------------------------------------------------
    def add_middleware(self, middleware_class: Callable[..., Any], **kwargs: Any) -> None:
        """Register middleware (stored for introspection only)."""

        middleware = middleware_class(self, **kwargs)
        self._middleware.append(middleware)

    # ------------------------------------------------------------------
    def _route(self, path: str, methods: Iterable[str], **metadata: Any) -> Callable:
        def decorator(func: Callable) -> Callable:
            self._routes.append({"path": path, "methods": tuple(methods), "func": func, "meta": metadata})
            return func

        return decorator

    def get(self, path: str, **metadata: Any) -> Callable:
        return self._route(path, ("GET",), **metadata)

    def post(self, path: str, **metadata: Any) -> Callable:
        return self._route(path, ("POST",), **metadata)

    def put(self, path: str, **metadata: Any) -> Callable:  # pragma: no cover - unused in tests
        return self._route(path, ("PUT",), **metadata)

    def delete(self, path: str, **metadata: Any) -> Callable:  # pragma: no cover - unused in tests
        return self._route(path, ("DELETE",), **metadata)

    # FastAPI exposes router dependency injection helpers as methods on the
    # app object.  The tests do not rely on these but we provide placeholders to
    # keep attribute lookups working if the code accesses them.
    def on_event(self, _event: str) -> Callable[[Callable], Callable]:  # pragma: no cover - defensive
        def decorator(func: Callable) -> Callable:
            return func

        return decorator

