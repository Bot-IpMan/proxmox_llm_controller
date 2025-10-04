import socket

import pytest

from pydantic import ValidationError

from controller.app import CreateLXCReq


def _base_payload(**overrides):
    payload = {
        "vmid": 101,
        "hostname": "example.com",
        "storage": "local-lvm",
        "ostemplate": "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
    }
    payload.update(overrides)
    return payload


def test_hostname_validation_requires_resolvable_host(monkeypatch):
    def fake_getaddrinfo(host, *_args, **_kwargs):
        raise socket.gaierror("name or service not known")

    monkeypatch.setattr("controller.app.socket.getaddrinfo", fake_getaddrinfo)

    with pytest.raises(ValidationError) as exc:
        CreateLXCReq(**_base_payload(hostname="unresolvable"))

    assert "cannot be resolved" in str(exc.value)


def test_hostname_validation_allows_resolvable_host(monkeypatch):
    def fake_getaddrinfo(host, *_args, **_kwargs):
        return [(socket.AF_INET, None, None, None, ("192.0.2.10", 0))]

    monkeypatch.setattr("controller.app.socket.getaddrinfo", fake_getaddrinfo)

    spec = CreateLXCReq(**_base_payload(hostname="valid.example"))

    assert spec.hostname == "valid.example"

