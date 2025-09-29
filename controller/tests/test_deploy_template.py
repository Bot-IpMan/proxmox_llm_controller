from __future__ import annotations

from pathlib import Path
from typing import List

import pytest

from controller import app


class DummyResult:
    def __init__(self, returncode: int = 0, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def reset_bliss_openapi_cache() -> None:
    app._load_bliss_openapi.cache_clear()


def test_deploy_renders_placeholders_with_whitespace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: List[List[str]] = []

    key_path = tmp_path / "id_rsa"
    key_path.write_text("dummy", encoding="utf-8")

    monkeypatch.setattr(app, "_require_pve_ssh", lambda: ("host", "user", str(key_path)))

    def fake_run(cmd, *, capture_output, text, timeout, **_kwargs):  # type: ignore[no-untyped-def]
        calls.append(cmd)
        return DummyResult(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(app.subprocess, "run", fake_run)

    spec = app.DeploySpec(
        target_vmid=101,
        repo_url="https://example.com/repo.git",
        workdir="/opt/app",
        setup=[],
        commands=["echo {{ repo_url }} {{ workdir }}"],
    )

    result = app.deploy(spec)

    assert result["ok"] is True
    assert calls, "Expected deploy to invoke ssh command"
    assert result["steps"][0]["cmd"] == "echo https://example.com/repo.git /opt/app"
    assert "{{" not in result["steps"][0]["cmd"]
