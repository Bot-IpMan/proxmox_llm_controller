from typing import Dict

import pytest

from controller import app


@pytest.fixture(autouse=True)
def clear_operation_log() -> None:
    app._clear_operation_log()
    yield
    app._clear_operation_log()


class DummyLXCResource:
    def __init__(self, recorder: Dict[str, object]) -> None:
        self._recorder = recorder

    def post(self, **payload: object) -> Dict[str, str]:
        self._recorder["payload"] = payload
        return {"upid": "UPID:lxc/create"}


class DummyNodeResource:
    def __init__(self, recorder: Dict[str, object]) -> None:
        self.lxc = DummyLXCResource(recorder)


class DummyProxmox:
    def __init__(self, recorder: Dict[str, object]) -> None:
        self._recorder = recorder

    def nodes(self, node: str) -> DummyNodeResource:
        self._recorder["node"] = node
        return DummyNodeResource(self._recorder)


def test_create_lxc_records_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    recorder: Dict[str, object] = {}

    monkeypatch.setattr(app, "get_proxmox", lambda: DummyProxmox(recorder))
    monkeypatch.setenv("LXC_PASSWORD_MIN_LENGTH", "4")

    req = app.CreateLXCReq(
        node="bal",
        vmid=116,
        hostname="ct116",
        cores=1,
        memory=1024,
        storage="local-lvm",
        rootfs_gb=10,
        bridge="vmbr0",
        ip_cidr="dhcp",
        password="1023",
        ostemplate="local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst",
    )

    response = app.create_lxc(req)

    assert response["created"] is True
    assert recorder["payload"]["rootfs"] == "local-lvm:10"

    log_entries = app._get_operation_log()
    assert len(log_entries) == 1
    entry = log_entries[0]
    assert entry["kind"] == "lxc.create"
    assert entry["metadata"]["node"] == "bal"
    assert entry["metadata"]["vmid"] == 116
    assert entry["payload"]["rootfs"] == "local-lvm:10"
    assert entry["payload"]["password"] == "***"
    assert entry["result"]["task"] == {"upid": "UPID:lxc/create"}


def test_operation_logs_endpoint_returns_latest_entries() -> None:
    app._record_operation("demo", metadata={"step": 1})
    app._record_operation("demo", metadata={"step": 2})

    response = app.list_operation_logs(limit=1)

    assert response.count == 1
    assert response.entries[0].metadata["step"] == 2
