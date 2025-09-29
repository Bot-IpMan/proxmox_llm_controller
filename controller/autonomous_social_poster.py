"""High-level automation script for posting to social networks on BlissOS.

This module exposes an ergonomic wrapper around
``controller.bliss_social_automation`` so that a single, parameterised Python
script can automate common publishing workflows inside a BlissOS virtual
machine.  It supports installing applications, launching them, uploading media
assets, generating post copy with an LLM provider and finally sharing content
through Android intents.

The goal is to keep the script completely autonomous.  Every interaction with
the Android instance happens via ``adb`` (installation, removal, launching
activities and sending share intents).  The module therefore offers both a
class based API (:class:`AutonomousSocialPoster`) and a command line interface
so it can be run unattended inside CI/CD pipelines or scheduled tasks.
"""

from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, MutableMapping, Optional, Sequence

from .bliss_social_automation import (
    ADBCommandError,
    ADBClient,
    BlissSocialAutomation,
    ContentGenerator,
    SOCIAL_APPS,
    SocialAppConfig,
)

__all__ = [
    "NetworkParameters",
    "NETWORKS",
    "AutonomousSocialPoster",
    "build_arg_parser",
    "main",
]


@dataclass(frozen=True)
class NetworkParameters:
    """Descriptor containing automation metadata for a social network."""

    name: str
    app: SocialAppConfig
    remote_directory: str = "/sdcard/Download"
    media_prefix: str = "post"


NETWORKS: Dict[str, NetworkParameters] = {
    name: NetworkParameters(name=name, app=config, media_prefix=f"{name}_post")
    for name, config in SOCIAL_APPS.items()
}


class AutonomousSocialPoster:
    """High level orchestration helper around :class:`BlissSocialAutomation`."""

    def __init__(
        self,
        automation: Optional[BlissSocialAutomation] = None,
        *,
        adb_client: Optional[ADBClient] = None,
    ) -> None:
        self.automation = automation or BlissSocialAutomation(adb_client or ADBClient())

    # ──────────────────────────────────────────────────────────────────
    # Network metadata helpers
    # ──────────────────────────────────────────────────────────────────

    def get_network(self, network: str) -> NetworkParameters:
        try:
            return NETWORKS[network.lower()]
        except KeyError as exc:
            raise KeyError(
                f"Unknown social network '{network}'. Available: {', '.join(sorted(NETWORKS))}"
            ) from exc

    # ──────────────────────────────────────────────────────────────────
    # App lifecycle operations
    # ──────────────────────────────────────────────────────────────────

    def install_app(self, network: str, apk_path: Optional[Path] = None, *, reinstall: bool = False) -> str:
        """Install or update an application for the requested network."""

        profile = self.get_network(network)
        if apk_path is None:
            self.automation.ensure_app_installed(profile.app)
            return f"{profile.app.package} already installed"
        return self.automation.install_app(Path(apk_path), reinstall=reinstall)

    def uninstall_app(self, network: str, *, keep_data: bool = False) -> str:
        profile = self.get_network(network)
        return self.automation.uninstall_app(profile.app.package, keep_data=keep_data)

    def launch_app(self, network: str, *, activity: Optional[str] = None) -> str:
        profile = self.get_network(network)
        return self.automation.launch_app(profile.app, activity=activity)

    def force_stop(self, network: str) -> None:
        profile = self.get_network(network)
        self.automation.force_stop(profile.app)

    # ──────────────────────────────────────────────────────────────────
    # Content helpers
    # ──────────────────────────────────────────────────────────────────

    def push_content(
        self,
        network: str,
        files: Iterable[Path | str],
        *,
        remote_directory: Optional[str] = None,
    ) -> Dict[str, str]:
        profile = self.get_network(network)
        destination = remote_directory or profile.remote_directory
        paths = [Path(item) for item in files]
        return self.automation.push_assets(paths, remote_directory=destination)

    def generate_content(
        self,
        network: str,
        prompt: str,
        *,
        system_prompt: Optional[str] = None,
        generator: Optional[ContentGenerator] = None,
        generator_options: Optional[Mapping[str, Any]] = None,
    ) -> str:
        self.get_network(network)  # validation only
        return self.automation.generate_post_text(
            prompt,
            generator=generator,
            generator_options=generator_options,
            system_prompt=system_prompt,
        )

    def post_content(
        self,
        network: str,
        *,
        text: Optional[str] = None,
        media: Sequence[Path | str] = (),
        subject: Optional[str] = None,
        extras: Optional[MutableMapping[str, str]] = None,
        remote_directory: Optional[str] = None,
        share_activity: Optional[str] = None,
        generation_prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        generator: Optional[ContentGenerator] = None,
        generator_options: Optional[Mapping[str, Any]] = None,
    ) -> str:
        profile = self.get_network(network)
        target_dir = remote_directory or profile.remote_directory
        media_paths = [Path(item) for item in media]
        return self.automation.publish_post(
            profile.name,
            text=text,
            subject=subject,
            media=media_paths,
            extras=extras,
            remote_directory=target_dir,
            share_activity=share_activity,
            generation_prompt=generation_prompt,
            system_prompt=system_prompt,
            generator=generator,
            generator_options=generator_options,
        )

    def run_plan(self, plan: Sequence[Mapping[str, Any]], *, stop_on_error: bool = False) -> Sequence[Dict[str, Any]]:
        for entry in plan:
            if "app" not in entry:
                raise KeyError("Each plan entry must include the 'app' field")
            self.get_network(str(entry["app"]))  # validate network name
        return self.automation.publish_batch(plan, stop_on_error=stop_on_error)

    # ──────────────────────────────────────────────────────────────────
    # Device helpers
    # ──────────────────────────────────────────────────────────────────

    def list_devices(self) -> Sequence[Dict[str, str]]:
        return self.automation.adb.list_devices()

    def ensure_device(self) -> None:
        self.automation.ensure_device()


def _parse_extras(pairs: Sequence[str]) -> Dict[str, str]:
    extras: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"Invalid extra '{item}'. Expected KEY=VALUE format")
        key, value = item.split("=", 1)
        extras[key] = value
    return extras


def _load_plan(path: Path) -> Sequence[Mapping[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        posts = data.get("posts")
        if isinstance(posts, list):
            return posts
    raise ValueError("Plan must be a list or an object containing a 'posts' list")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb-path", dest="adb_path", help="Path to adb binary")
    parser.add_argument("--serial", help="ADB serial number")
    parser.add_argument("--connect", help="ADB connect address (HOST:PORT)")
    parser.add_argument("--timeout", type=int, default=60, help="Default adb timeout in seconds")
    parser.add_argument("--log-level", default="INFO", help="Python logging level")
    parser.add_argument("--log-file", help="File path for log output")

    subparsers = parser.add_subparsers(dest="command")

    devices_parser = subparsers.add_parser("devices", help="List detected adb devices")
    devices_parser.set_defaults(command="devices")

    install_parser = subparsers.add_parser("install", help="Install or update an application")
    install_parser.add_argument("network", choices=sorted(NETWORKS))
    install_parser.add_argument("--apk", type=Path, help="Local path to APK file")
    install_parser.add_argument("--reinstall", action="store_true", help="Pass -r to adb install")

    uninstall_parser = subparsers.add_parser("uninstall", help="Remove an installed application")
    uninstall_parser.add_argument("network", choices=sorted(NETWORKS))
    uninstall_parser.add_argument("--keep-data", action="store_true", help="Preserve app data during uninstall")

    launch_parser = subparsers.add_parser("launch", help="Launch a social media application")
    launch_parser.add_argument("network", choices=sorted(NETWORKS))
    launch_parser.add_argument("--activity", help="Override launch activity component")

    force_parser = subparsers.add_parser("force-stop", help="Force stop a running application")
    force_parser.add_argument("network", choices=sorted(NETWORKS))

    push_parser = subparsers.add_parser("push", help="Upload media files to the device")
    push_parser.add_argument("network", choices=sorted(NETWORKS))
    push_parser.add_argument("files", nargs="+", type=Path, help="Local files to push")
    push_parser.add_argument("--remote-dir", help="Destination directory on the device")

    post_parser = subparsers.add_parser("post", help="Publish content to a network")
    post_parser.add_argument("network", choices=sorted(NETWORKS))
    post_parser.add_argument("--text", help="Post body text")
    post_parser.add_argument("--subject", help="Optional subject/title")
    post_parser.add_argument("--media", nargs="*", type=Path, default=[], help="Media files to attach")
    post_parser.add_argument("--remote-dir", help="Remote directory for media uploads")
    post_parser.add_argument("--share-activity", help="Override share activity component")
    post_parser.add_argument("--extra", action="append", default=[], metavar="KEY=VALUE", help="Additional intent extras")
    post_parser.add_argument("--prompt", dest="generation_prompt", help="LLM prompt for auto-generated text")
    post_parser.add_argument("--system-prompt", dest="system_prompt", help="Optional system prompt for LLM")
    post_parser.add_argument("--llm-provider", choices=["openai", "huggingface"], help="LLM provider to use")
    post_parser.add_argument("--llm-model", help="Specific LLM model identifier")
    post_parser.add_argument("--llm-temperature", type=float, help="Sampling temperature for generation")
    post_parser.add_argument("--llm-max-tokens", type=int, help="Maximum number of tokens to generate")

    generate_parser = subparsers.add_parser("generate", help="Generate post copy without publishing")
    generate_parser.add_argument("network", choices=sorted(NETWORKS))
    generate_parser.add_argument("prompt", help="Prompt for the generator")
    generate_parser.add_argument("--system-prompt", dest="system_prompt", help="System prompt for the generator")
    generate_parser.add_argument("--llm-provider", choices=["openai", "huggingface"], help="Override LLM provider")
    generate_parser.add_argument("--llm-model", help="Model identifier")
    generate_parser.add_argument("--llm-temperature", type=float, help="Sampling temperature")
    generate_parser.add_argument("--llm-max-tokens", type=int, help="Maximum tokens/new tokens")

    batch_parser = subparsers.add_parser("batch", help="Execute a JSON automation plan")
    batch_parser.add_argument("plan", type=Path, help="Path to plan JSON file")
    batch_parser.add_argument("--stop-on-error", action="store_true", help="Abort when a post fails")

    return parser


def _generator_options_from_args(options: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    if getattr(options, "llm_provider", None):
        config["provider"] = options.llm_provider
    if getattr(options, "llm_model", None):
        config["model"] = options.llm_model
    if getattr(options, "llm_temperature", None) is not None:
        config["temperature"] = options.llm_temperature
    if getattr(options, "llm_max_tokens", None) is not None:
        config["max_tokens"] = options.llm_max_tokens
    return config


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    options = parser.parse_args(argv)

    logging_kwargs: Dict[str, Any] = {
        "level": getattr(logging, options.log_level.upper(), logging.INFO),
        "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    }
    if options.log_file:
        logging_kwargs["filename"] = options.log_file
    else:
        logging_kwargs["stream"] = sys.stderr
    logging.basicConfig(**logging_kwargs)
    log = logging.getLogger("autonomous-social-poster")

    adb_client = ADBClient(
        adb_path=options.adb_path,
        serial=options.serial,
        connect_address=options.connect,
        default_timeout=options.timeout,
    )
    poster = AutonomousSocialPoster(adb_client=adb_client)

    try:
        if options.command == "devices":
            print(json.dumps(poster.list_devices(), indent=2))
            return 0

        if options.command == "install":
            print(poster.install_app(options.network, options.apk, reinstall=options.reinstall))
            return 0

        if options.command == "uninstall":
            print(poster.uninstall_app(options.network, keep_data=options.keep_data))
            return 0

        if options.command == "launch":
            print(poster.launch_app(options.network, activity=options.activity))
            return 0

        if options.command == "force-stop":
            poster.force_stop(options.network)
            log.info("Force stopped %s", options.network)
            return 0

        if options.command == "push":
            uploads = poster.push_content(options.network, options.files, remote_directory=options.remote_dir)
            print(json.dumps(uploads, indent=2))
            return 0

        if options.command == "post":
            extras = _parse_extras(options.extra)
            generator_options = _generator_options_from_args(options)
            media_paths = [Path(p) for p in options.media]
            result = poster.post_content(
                options.network,
                text=options.text,
                subject=options.subject,
                media=media_paths,
                remote_directory=options.remote_dir,
                share_activity=options.share_activity,
                extras=extras,
                generation_prompt=options.generation_prompt,
                system_prompt=options.system_prompt,
                generator_options=generator_options or None,
            )
            print(result)
            return 0

        if options.command == "generate":
            generator_options = _generator_options_from_args(options)
            result = poster.generate_content(
                options.network,
                options.prompt,
                system_prompt=options.system_prompt,
                generator_options=generator_options or None,
            )
            print(result)
            return 0

        if options.command == "batch":
            plan = _load_plan(options.plan)
            results = poster.run_plan(plan, stop_on_error=options.stop_on_error)
            print(json.dumps(results, indent=2))
            return 0

    except (ADBCommandError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:  # type: ignore[name-defined]
        log.error("Automation failed: %s", exc)
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
