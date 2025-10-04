import pytest
from fastapi import HTTPException
from proxmoxer.core import ResourceException

from controller import app


@pytest.fixture(autouse=True)
def clear_operation_log() -> None:
    app._clear_operation_log()
    yield
    app._clear_operation_log()


class FailingLXCResource:
    def post(self, **payload: object) -> None:
        raise ResourceException(
            400,
            "Bad Request",
            "Parameter verification failed.",
            {"storage": "storage 'local' does not support container directories"},
        )


class FailingNodeResource:
    def __init__(self) -> None:
        self.lxc = FailingLXCResource()


class FailingProxmox:
    def nodes(self, node: str) -> FailingNodeResource:
        return FailingNodeResource()


def test_create_lxc_surfaces_proxmox_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "get_proxmox", lambda: FailingProxmox())
    monkeypatch.setattr(app, "_default_node", lambda prox, node: "pve")

    req = app.CreateLXCReq(
        node=None,
        vmid=1234,
        hostname="new-vm",
        cores=2,
        memory=2048,
        storage="local",
        rootfs_gb=8,
        bridge="vmbr0",
        ip_cidr="dhcp",
        ostemplate="local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    )

    with pytest.raises(HTTPException) as excinfo:
        app.create_lxc(req)

    error = excinfo.value
    assert error.status_code == 400
    assert "/lxc/create failed" in error.detail
    assert "storage 'local' does not support container directories" in error.detail

    entries = app._get_operation_log()
    assert len(entries) == 1
    assert entries[0]["error"].startswith("400 Bad Request: Parameter verification failed.")
