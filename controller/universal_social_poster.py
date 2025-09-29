"""High level automation helpers for posting to multiple social networks on BlissOS.

This module builds upon :mod:`controller.bliss_social_automation` and exposes a
thin, reusable facade with a configuration driven approach.  Each supported
network is described in :data:`SOCIAL_NETWORKS` and the module offers a set of
shared functions (``install_app``, ``launch_app``, ``upload_file``,
``post_content`` and ``generate_content``) that accept the social network name as
an argument.  This keeps the workflow parameterised and makes it trivial to
extend with new networks by simply updating the configuration dictionary.

The functions are intentionally lightweight – they defer the heavy lifting to
:class:`controller.bliss_social_automation.BlissSocialAutomation` while
normalising default arguments and ensuring the same calling convention is
available across all supported networks.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

from .bliss_social_automation import (
    ADBClient,
    BlissSocialAutomation,
    ContentGenerator,
    SOCIAL_APPS,
    SocialAppConfig,
)

__all__ = [
    "NetworkConfig",
    "SOCIAL_NETWORKS",
    "get_network_config",
    "ensure_automation",
    "install_app",
    "launch_app",
    "upload_file",
    "post_content",
    "generate_content",
]


@dataclass(frozen=True)
class NetworkConfig:
    """Descriptor for an individual social network automation profile."""

    name: str
    app: SocialAppConfig
    remote_directory: str = "/sdcard/Download"
    media_prefix: str = "post"


SOCIAL_NETWORKS: Dict[str, NetworkConfig] = {
    name: NetworkConfig(name=name, app=config, media_prefix=f"{name}_post")
    for name, config in SOCIAL_APPS.items()
}


def get_network_config(network: str) -> NetworkConfig:
    """Return the :class:`NetworkConfig` for the requested network."""

    try:
        return SOCIAL_NETWORKS[network.lower()]
    except KeyError as exc:  # pragma: no cover - invalid user input
        raise KeyError(
            f"Unknown network '{network}'. Available: {', '.join(sorted(SOCIAL_NETWORKS))}"
        ) from exc


def ensure_automation(
    automation: Optional[BlissSocialAutomation] = None,
    *,
    adb_client: Optional[ADBClient] = None,
) -> BlissSocialAutomation:
    """Return a ready to use :class:`BlissSocialAutomation` instance."""

    if automation is not None:
        return automation
    return BlissSocialAutomation(adb_client or ADBClient())


def install_app(
    network: str,
    apk_path: Optional[Path] = None,
    *,
    reinstall: bool = False,
    automation: Optional[BlissSocialAutomation] = None,
    adb_client: Optional[ADBClient] = None,
) -> str:
    """Install or update a social media application on the BlissOS device."""

    config = get_network_config(network)
    controller = ensure_automation(automation, adb_client=adb_client)

    if apk_path is None:
        controller.ensure_app_installed(config.app)
        return f"{config.app.package} already installed"

    return controller.install_app(Path(apk_path), reinstall=reinstall)


def launch_app(
    network: str,
    *,
    activity: Optional[str] = None,
    automation: Optional[BlissSocialAutomation] = None,
    adb_client: Optional[ADBClient] = None,
) -> str:
    """Launch the specified social media application."""

    config = get_network_config(network)
    controller = ensure_automation(automation, adb_client=adb_client)
    return controller.launch_app(config.app, activity=activity)


def upload_file(
    network: str,
    files: Iterable[Path | str],
    *,
    remote_directory: Optional[str] = None,
    automation: Optional[BlissSocialAutomation] = None,
    adb_client: Optional[ADBClient] = None,
) -> Dict[str, str]:
    """Push local files onto the BlissOS device for a given social network."""

    config = get_network_config(network)
    controller = ensure_automation(automation, adb_client=adb_client)
    destination = remote_directory or config.remote_directory
    resolved_files = [Path(item) for item in files]
    return controller.push_assets(resolved_files, remote_directory=destination)


def post_content(
    network: str,
    *,
    text: Optional[str] = None,
    subject: Optional[str] = None,
    media: Sequence[Path | str] = (),
    generation_prompt: Optional[str] = None,
    system_prompt: Optional[str] = None,
    generator: Optional[ContentGenerator] = None,
    generator_options: Optional[Mapping[str, Any]] = None,
    extras: Optional[MutableMapping[str, str]] = None,
    remote_directory: Optional[str] = None,
    share_activity: Optional[str] = None,
    automation: Optional[BlissSocialAutomation] = None,
    adb_client: Optional[ADBClient] = None,
) -> str:
    """Publish a post to the selected network using the shared automation API."""

    config = get_network_config(network)
    controller = ensure_automation(automation, adb_client=adb_client)
    target_directory = remote_directory or config.remote_directory
    media_paths = [Path(item) for item in media]

    return controller.publish_post(
        config.name,
        text=text,
        generation_prompt=generation_prompt,
        system_prompt=system_prompt,
        generator=generator,
        generator_options=generator_options,
        subject=subject,
        media=media_paths,
        remote_directory=target_directory,
        share_activity=share_activity,
        extras=extras,
    )


def generate_content(
    network: str,
    prompt: str,
    *,
    system_prompt: Optional[str] = None,
    automation: Optional[BlissSocialAutomation] = None,
    adb_client: Optional[ADBClient] = None,
    generator: Optional[ContentGenerator] = None,
    generator_options: Optional[Mapping[str, Any]] = None,
) -> str:
    """Generate social media copy in a network agnostic fashion."""

    _ = get_network_config(network)  # Validate network name for consistency
    controller = ensure_automation(automation, adb_client=adb_client)
    return controller.generate_post_text(
        prompt,
        generator=generator,
        generator_options=generator_options,
        system_prompt=system_prompt,
    )
