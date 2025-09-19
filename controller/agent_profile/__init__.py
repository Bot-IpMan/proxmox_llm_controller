"""Agent profile assets and helpers."""

import os
from functools import lru_cache
from importlib import resources
from typing import Dict, Any

PROFILE_VERSION = "1.0.0"
AGENT_NAME = "Proxmox Infrastructure Management Agent"
AGENT_DESCRIPTION = (
    "Autonomous root-level operator for Proxmox VE controlled through the "
    "Universal LLM Controller API."
)


def _read_text(asset_name: str) -> str:
    try:
        with resources.files(__name__).joinpath(asset_name).open("r", encoding="utf-8") as handle:
            return handle.read().strip()
    except FileNotFoundError as exc:  # pragma: no cover - defensive guard
        raise RuntimeError(f"Agent profile asset '{asset_name}' is missing") from exc


@lru_cache(maxsize=1)
def get_agent_profile() -> Dict[str, Any]:
    """Return the static agent profile used by LLM front-ends."""
    base_url = os.getenv("AGENT_CONTROLLER_URL", "http://proxmox-controller:8000")
    openapi_url = os.getenv(
        "AGENT_OPENAPI_URL",
        f"{base_url.rstrip('/')}/openapi.json",
    )

    profile: Dict[str, Any] = {
        "name": AGENT_NAME,
        "description": AGENT_DESCRIPTION,
        "version": PROFILE_VERSION,
        "controller": {
            "base_url": base_url,
            "openapi_url": openapi_url,
        },
        "defaults": {
            "vmid_range": "100-999",
            "network": "192.168.1.0/24",
            "gateway": "192.168.1.1",
            "bridge": "vmbr0",
            "storage": "local-lvm",
            "templates": [
                "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst",
                "local:vztmpl/ubuntu-22.04-standard_22.04-1_amd64.tar.zst",
            ],
        },
        "system_prompt": _read_text("system_prompt.md"),
        "quick_reference": _read_text("action_recipes.md"),
    }
    return profile


__all__ = ["get_agent_profile", "AGENT_NAME", "PROFILE_VERSION"]
