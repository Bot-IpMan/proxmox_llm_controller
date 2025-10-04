"""Test helpers for environments without optional dependencies."""
from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:  # pragma: no cover - depends on runner
    sys.path.insert(0, str(ROOT))

try:  # pragma: no cover - prefer the real dependency when available
    importlib.import_module("pydantic")
except ModuleNotFoundError:  # pragma: no cover - exercised in tests
    fallback_path = ROOT.joinpath("controller", "_pydantic_fallback.py")
    spec = importlib.util.spec_from_file_location("_pydantic_fallback", fallback_path)
    if spec and spec.loader:  # pragma: no branch - defensive guard
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        sys.modules.setdefault("pydantic", module)
