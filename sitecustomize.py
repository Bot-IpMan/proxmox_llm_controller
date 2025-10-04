"""Test helpers for environments without optional dependencies."""
from __future__ import annotations

import importlib
import importlib.util
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:  # pragma: no cover - depends on runner
    sys.path.insert(0, str(ROOT))

def _load_module(name: str, fallback_relative: str) -> object:
    fallback_path = ROOT.joinpath(fallback_relative)
    spec = importlib.util.spec_from_file_location(f"_{name}_fallback", fallback_path)
    if not spec or not spec.loader:  # pragma: no cover - defensive
        raise ModuleNotFoundError(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


try:  # pragma: no cover - prefer the real dependency when available
    importlib.import_module("pydantic")
except ModuleNotFoundError:  # pragma: no cover - exercised in tests
    module = _load_module("pydantic", "controller/_pydantic_fallback.py")
    sys.modules.setdefault("pydantic", module)

try:  # pragma: no cover - prefer the real dependency when available
    importlib.import_module("fastapi")
except ModuleNotFoundError:  # pragma: no cover - exercised in tests
    fastapi_fallback = _load_module("fastapi", "controller/_fastapi_fallback.py")
    fastapi_module = types.ModuleType("fastapi")
    fastapi_module.FastAPI = fastapi_fallback.FastAPI
    fastapi_module.HTTPException = fastapi_fallback.HTTPException
    fastapi_module.Query = fastapi_fallback.Query
    fastapi_module.CORSMiddleware = fastapi_fallback.CORSMiddleware
    fastapi_module.__all__ = getattr(fastapi_fallback, "__all__", [])
    fastapi_module.__path__ = []  # type: ignore[attr-defined]

    middleware_pkg = types.ModuleType("fastapi.middleware")
    cors_module = types.ModuleType("fastapi.middleware.cors")
    cors_module.CORSMiddleware = fastapi_fallback.CORSMiddleware
    cors_module.__all__ = ["CORSMiddleware"]
    middleware_pkg.cors = cors_module
    fastapi_module.middleware = middleware_pkg  # type: ignore[attr-defined]

    sys.modules.setdefault("fastapi", fastapi_module)
    sys.modules.setdefault("fastapi.middleware", middleware_pkg)
    sys.modules.setdefault("fastapi.middleware.cors", cors_module)

try:  # pragma: no cover - prefer the real dependency when available
    importlib.import_module("proxmoxer")
except ModuleNotFoundError:  # pragma: no cover - exercised in tests
    proxmoxer_fallback = _load_module("proxmoxer", "controller/_proxmoxer_fallback.py")
    proxmoxer_pkg = types.ModuleType("proxmoxer")
    proxmoxer_pkg.ProxmoxAPI = proxmoxer_fallback.ProxmoxAPI
    proxmoxer_pkg.ResourceException = proxmoxer_fallback.ResourceException
    proxmoxer_pkg.__all__ = ["ProxmoxAPI", "ResourceException"]
    proxmoxer_pkg.__path__ = []  # type: ignore[attr-defined]

    core_module = types.ModuleType("proxmoxer.core")
    core_module.ResourceException = proxmoxer_fallback.ResourceException
    core_module.__all__ = ["ResourceException"]
    proxmoxer_pkg.core = core_module

    sys.modules.setdefault("proxmoxer", proxmoxer_pkg)
    sys.modules.setdefault("proxmoxer.core", core_module)
