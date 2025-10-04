"""Test configuration helpers."""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:  # pragma: no cover - depends on runner
    sys.path.insert(0, str(PROJECT_ROOT))
