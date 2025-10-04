"""Tests for configurable LXC password validation."""

from __future__ import annotations

import pytest

from controller.app import CreateLXCReq


def _base_payload(**overrides):
    payload = {
        "vmid": 999,
        "hostname": "ct-999",
        "cores": 1,
        "memory": 512,
        "storage": "local-lvm",
        "rootfs_gb": 8,
        "bridge": "vmbr0",
        "ip_cidr": "dhcp",
        "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    }
    payload.update(overrides)
    return payload


def test_password_uses_default_minimum_length(monkeypatch):
    monkeypatch.delenv("LXC_PASSWORD_MIN_LENGTH", raising=False)

    with pytest.raises(ValueError) as excinfo:
        CreateLXCReq(**_base_payload(password="1234"))

    assert "at least 5 characters" in str(excinfo.value)


def test_password_respects_environment_override(monkeypatch):
    monkeypatch.setenv("LXC_PASSWORD_MIN_LENGTH", "4")

    req = CreateLXCReq(**_base_payload(password="1023"))

    assert req.password == "1023"
