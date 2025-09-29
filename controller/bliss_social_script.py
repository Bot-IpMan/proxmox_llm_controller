"""Utility wrappers for automating social media posts on BlissOS.

This module exposes a compact, function-based façade around
``controller.bliss_social_automation`` so that external automation (for
example CI jobs or conversational agents) can orchestrate the full posting
workflow with only a handful of helper calls.  The design follows the brief
from the technical specification: each supported social network is described
via a configuration dictionary and the public API consists of five
high-level helpers – :func:`install_app`, :func:`launch_app`,
:func:`upload_file`, :func:`post_to_social` and :func:`generate_content`.

All functions are defensive: they validate inputs, normalise paths, convert
simple dictionaries into :class:`pathlib.Path` instances and wrap ADB/LLM
errors in readable :class:`RuntimeError` exceptions.  This keeps the module
easy to embed in bigger systems that expect a single entry point which does
not leak vendor specific exceptions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

from .bliss_social_automation import (
    ADBClient,
    ADBCommandError,
    BlissSocialAutomation,
    ContentGenerator,
    ContentGeneratorError,
    SOCIAL_APPS,
    SocialAppConfig,
)

__all__ = [
    "SocialNetworkConfig",
    "PostRequest",
    "SOCIAL_NETWORKS",
    "ensure_automation",
    "install_app",
    "launch_app",
    "upload_file",
    "post_to_social",
    "generate_content",
]


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SocialNetworkConfig:
    """Descriptor for a supported social media network."""

    name: str
    app: SocialAppConfig
    remote_directory: str = "/sdcard/Download"
    media_prefix: str = field(default_factory=lambda: "post")

    def as_dict(self) -> Dict[str, Any]:
        """Return a serialisable representation used by external callers."""

        return {
            "name": self.name,
            "package_name": self.app.package,
            "launch_activity": self.app.launch_activity,
            "share_activity": self.app.share_activity,
            "share_action": self.app.share_action,
            "content_type": self.app.default_mime_type,
            "supports_multiple": self.app.supports_multiple,
            "extra_flags": list(self.app.extra_flags),
            "allow_text_extra": self.app.allow_text_extra,
            "grant_read_uri_permission": self.app.grant_read_uri_permission,
            "share_categories": list(self.app.share_categories),
            "remote_directory": self.remote_directory,
            "media_prefix": self.media_prefix,
        }


SOCIAL_NETWORKS: Dict[str, SocialNetworkConfig] = {
    name: SocialNetworkConfig(name=name, app=config, media_prefix=f"{name}_post")
    for name, config in SOCIAL_APPS.items()
}


@dataclass
class PostRequest:
    """Container describing a social media post request."""

    text: Optional[str] = None
    subject: Optional[str] = None
    media: Sequence[Path | str] = ()
    extras: Optional[Mapping[str, str]] = None
    remote_directory: Optional[str] = None
    share_activity: Optional[str] = None
    generation_prompt: Optional[str] = None
    system_prompt: Optional[str] = None
    generator: Optional[ContentGenerator] = None
    generator_options: Optional[Mapping[str, Any]] = None
    launch_before_share: bool = False
    launch_activity: Optional[str] = None


def ensure_automation(
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
) -> BlissSocialAutomation:
    """Return a :class:`BlissSocialAutomation` instance for the given input."""

    if isinstance(device, BlissSocialAutomation):
        return device
    if isinstance(device, ADBClient):
        return BlissSocialAutomation(device)
    if device is None:
        return BlissSocialAutomation(ADBClient())
    # Allow duck-typed fakes in tests (must expose the required methods)
    if hasattr(device, "install_app") and hasattr(device, "launch_app"):
        return device  # type: ignore[return-value]
    raise TypeError(
        "device must be BlissSocialAutomation, ADBClient or an automation-compatible object"
    )


def _get_config(network: str) -> SocialNetworkConfig:
    try:
        return SOCIAL_NETWORKS[network.lower()]
    except KeyError as exc:  # pragma: no cover - defensive branch
        raise KeyError(
            f"Unknown social network '{network}'. Available: {', '.join(sorted(SOCIAL_NETWORKS))}"
        ) from exc


def install_app(
    network: str,
    apk_path: Optional[Path | str] = None,
    *,
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
    reinstall: bool = False,
) -> str:
    """Install or update the application for the selected network."""

    automation = ensure_automation(device)
    config = _get_config(network)

    try:
        if apk_path is None:
            automation.ensure_app_installed(config.app)
            return f"{config.app.package} already installed"
        path = Path(apk_path)
        return automation.install_app(path, reinstall=reinstall)
    except ADBCommandError as exc:
        logger.error("Failed to install %s: %s", network, exc)
        raise RuntimeError(f"Failed to install {network}: {exc}") from exc


def launch_app(
    network: str,
    *,
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
    activity: Optional[str] = None,
) -> str:
    """Launch the target social media application."""

    automation = ensure_automation(device)
    config = _get_config(network)

    try:
        return automation.launch_app(config.app, activity=activity)
    except ADBCommandError as exc:
        logger.error("Failed to launch %s: %s", network, exc)
        raise RuntimeError(f"Failed to launch {network}: {exc}") from exc


def upload_file(
    network: str,
    files: Iterable[Path | str],
    *,
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
    remote_directory: Optional[str] = None,
) -> Dict[str, str]:
    """Upload media assets to the BlissOS device for the requested network."""

    automation = ensure_automation(device)
    config = _get_config(network)
    destination = remote_directory or config.remote_directory
    paths = [Path(item) for item in files]

    try:
        return automation.push_assets(paths, remote_directory=destination)
    except ADBCommandError as exc:
        logger.error("Failed to upload files for %s: %s", network, exc)
        raise RuntimeError(f"Failed to upload files for {network}: {exc}") from exc


def post_to_social(
    network: str,
    request: PostRequest,
    *,
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
) -> str:
    """Publish content to a social network using the configured automation."""

    automation = ensure_automation(device)
    config = _get_config(network)

    media_paths = [Path(item) for item in request.media]
    remote_directory = request.remote_directory or config.remote_directory

    extras: Optional[MutableMapping[str, str]] = None
    if request.extras:
        extras = dict(request.extras)

    try:
        return automation.publish_post(
            config.name,
            text=request.text,
            subject=request.subject,
            media=media_paths,
            extras=extras,
            remote_directory=remote_directory,
            share_activity=request.share_activity,
            generation_prompt=request.generation_prompt,
            system_prompt=request.system_prompt,
            generator=request.generator,
            generator_options=request.generator_options,
            launch_before_share=request.launch_before_share,
            launch_activity=request.launch_activity,
        )
    except ADBCommandError as exc:
        logger.error("Failed to post to %s: %s", network, exc)
        raise RuntimeError(f"Failed to post to {network}: {exc}") from exc


def generate_content(
    prompt: str,
    *,
    device: Optional[BlissSocialAutomation | ADBClient | Any] = None,
    system_prompt: Optional[str] = None,
    generator: Optional[ContentGenerator] = None,
    generator_options: Optional[Mapping[str, Any]] = None,
) -> str:
    """Generate social media copy via the configured LLM provider."""

    automation = ensure_automation(device)

    try:
        return automation.generate_post_text(
            prompt,
            system_prompt=system_prompt,
            generator=generator,
            generator_options=generator_options,
        )
    except ContentGeneratorError as exc:
        logger.error("Content generation failed: %s", exc)
        raise RuntimeError(f"Failed to generate content: {exc}") from exc

