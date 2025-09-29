"""Tests for the optional BlissOS OpenAPI helpers."""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


def _fresh_app(monkeypatch: pytest.MonkeyPatch):
    """Reload the controller.app module with a clean environment."""

    monkeypatch.delenv("BLISS_OPENAPI_PATH", raising=False)
    import controller.app as app

    return importlib.reload(app)


def test_repo_root_candidate_is_detected(monkeypatch: pytest.MonkeyPatch):
    app = _fresh_app(monkeypatch)

    expected = Path(app.__file__).resolve().parent.parent / "openapi_bliss.json"
    assert expected in app._BLISS_OPENAPI_CANDIDATES

    if expected.exists():
        assert app.BLISS_OPENAPI_PATH == str(expected)
        assert app.BLISS_OPENAPI_AUTO is True
    else:  # pragma: no cover - repository missing optional file
        pytest.skip("Repository root openapi_bliss.json is not present")


def test_auto_discovers_openapi_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    app = _fresh_app(monkeypatch)

    candidate = tmp_path / "openapi_bliss.json"
    candidate.write_text(json.dumps({"info": {"title": "Bliss"}}), encoding="utf-8")

    monkeypatch.setattr(app, "_BLISS_OPENAPI_CANDIDATES", (candidate,))
    app.BLISS_OPENAPI_PATH, app.BLISS_OPENAPI_AUTO = app._resolve_bliss_openapi_path()
    app._load_bliss_openapi.cache_clear()

    status = app.bliss_openapi_status()

    assert status.configured is True
    assert status.auto_discovered is True
    assert status.exists is True
    assert status.loadable is True
    assert status.error is None
    assert status.path == str(candidate)

    spec = app.bliss_openapi_spec()
    assert spec["info"]["title"] == "Bliss"


def test_status_reports_missing_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    app = _fresh_app(monkeypatch)

    monkeypatch.setattr(app, "_BLISS_OPENAPI_CANDIDATES", (tmp_path / "missing.json",))
    app.BLISS_OPENAPI_PATH, app.BLISS_OPENAPI_AUTO = app._resolve_bliss_openapi_path()
    app._load_bliss_openapi.cache_clear()

    status = app.bliss_openapi_status()

    assert status.configured is False
    assert status.auto_discovered is False
    assert status.exists is False
    assert status.error.endswith("openapi_bliss.json was found.")

