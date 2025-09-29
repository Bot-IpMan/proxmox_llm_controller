"""Automation helpers for managing social media Android apps on BlissOS via ADB.

This module provides a small toolkit for interacting with a BlissOS virtual
machine running inside Hyper-V.  It focuses on repetitive tasks that appear in
end-to-end social media publishing pipelines such as installing client
applications, pushing media assets to the device and launching share intents in
popular networks (Facebook, Instagram, TikTok, Twitter/X, Reddit, LinkedIn,
Threads).

The goal is to keep the script fully self-contained so it can be executed from a
host system without external dependencies.  The :mod:`subprocess` module is used
for invoking ``adb`` and the utilities are exposed both as a Python API and a
command-line interface.  The CLI enables quick prototyping directly from a
terminal while the class-based API can be reused from higher level orchestration
code (for example inside an automation agent or scheduler).
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple

__all__ = [
    "ADBCommandError",
    "SocialAppConfig",
    "ADBClient",
    "ContentGenerator",
    "ContentGeneratorError",
    "BlissSocialAutomation",
    "SOCIAL_APPS",
    "build_arg_parser",
    "main",
]


class ADBCommandError(RuntimeError):
    """Raised when an ``adb`` invocation fails."""

    def __init__(self, command: Sequence[str], returncode: int, stdout: str, stderr: str):
        super().__init__(
            f"adb command failed with exit code {returncode}: {shlex.join(command)}\n"
            f"stdout: {stdout}\n"
            f"stderr: {stderr}"
        )
        self.command = list(command)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@dataclass(frozen=True)
class SocialAppConfig:
    """Metadata about a supported social media application."""

    package: str
    launch_activity: Optional[str] = None
    share_activity: Optional[str] = None
    default_mime_type: str = "text/plain"
    share_action: str = "android.intent.action.SEND"
    supports_multiple: bool = True
    extra_flags: Tuple[str, ...] = ()

    def component(self, activity: Optional[str]) -> Optional[str]:
        """Return ``package/activity`` if an activity is provided."""

        if activity:
            if "/" in activity:
                return activity
            return f"{self.package}/{activity}"
        if self.launch_activity:
            return f"{self.package}/{self.launch_activity}"
        return None

    def share_component(self, activity: Optional[str]) -> Optional[str]:
        """Return the component used for share intents."""

        if activity:
            if "/" in activity:
                return activity
            return f"{self.package}/{activity}"
        if self.share_activity:
            return f"{self.package}/{self.share_activity}"
        return None


SOCIAL_APPS: Dict[str, SocialAppConfig] = {
    "facebook": SocialAppConfig(
        package="com.facebook.katana",
        launch_activity="com.facebook.katana.IntentUriHandler",
        share_activity="com.facebook.composer.publish.ComposerPublishLauncherActivity",
        default_mime_type="text/plain",
    ),
    "instagram": SocialAppConfig(
        package="com.instagram.android",
        launch_activity="com.instagram.mainactivity.MainActivity",
        share_activity="com.instagram.share.handleractivity.ShareHandlerActivity",
        default_mime_type="image/*",
    ),
    "tiktok": SocialAppConfig(
        package="com.zhiliaoapp.musically",
        launch_activity="com.ss.android.ugc.aweme.main.MainActivity",
        share_activity="com.ss.android.ugc.aweme.share.ShareHandlerActivity",
        default_mime_type="video/*",
    ),
    "twitter": SocialAppConfig(
        package="com.twitter.android",
        launch_activity="com.twitter.app.main.MainActivity",
        share_activity="com.twitter.composer.ComposerActivity",
        default_mime_type="text/plain",
    ),
    "reddit": SocialAppConfig(
        package="com.reddit.frontpage",
        launch_activity="com.reddit.frontpage.StartActivity",
        share_activity="com.reddit.frontpage.ui.share.ShareHandlerActivity",
        default_mime_type="image/*",
    ),
    "linkedin": SocialAppConfig(
        package="com.linkedin.android",
        launch_activity="com.linkedin.android.authenticator.LaunchActivity",
        share_activity="com.linkedin.android.l2m.deeplink.DeepLinkHelperActivity",
        default_mime_type="text/plain",
    ),
    "threads": SocialAppConfig(
        package="com.instagram.barcelona",
        launch_activity="com.instagram.barcelona.app.BarcelonaActivity",
        share_activity="com.instagram.barcelona.share.BarcelonaShareHandlerActivity",
        default_mime_type="text/plain",
    ),
}


class ContentGeneratorError(RuntimeError):
    """Raised when LLM based content generation fails."""


def _read_env_float(name: str) -> Optional[float]:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError as exc:  # pragma: no cover - misconfigured environment
        raise ContentGeneratorError(f"Environment variable {name} must be a float") from exc


def _read_env_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    try:
        return int(value)
    except ValueError as exc:  # pragma: no cover - misconfigured environment
        raise ContentGeneratorError(f"Environment variable {name} must be an integer") from exc


@dataclass
class ContentGenerator:
    """Utility wrapper around OpenAI or Hugging Face text generation."""

    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    openai_api_key: Optional[str] = None
    huggingface_model: Optional[str] = None
    device: Optional[str] = None
    extra_options: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        provider_env = os.getenv("BLISS_LLM_PROVIDER")
        provider = (self.provider or provider_env or "openai").lower()
        if provider not in {"openai", "huggingface"}:
            raise ContentGeneratorError(
                f"Unsupported LLM provider '{provider}'. Expected 'openai' or 'huggingface'."
            )
        self.provider = provider

        if self.temperature is None:
            self.temperature = _read_env_float("BLISS_LLM_TEMPERATURE")
        if self.max_tokens is None:
            self.max_tokens = _read_env_int("BLISS_LLM_MAX_TOKENS")

        if provider == "openai":
            self._initialise_openai()
        else:
            self._initialise_huggingface()

    # ──────────────────────────────────────────────────────────────────
    # Provider initialisation helpers
    # ──────────────────────────────────────────────────────────────────

    def _initialise_openai(self) -> None:
        try:
            import openai  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ContentGeneratorError(
                "OpenAI python package is required for provider 'openai'."
            ) from exc

        api_key = self.openai_api_key or os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ContentGeneratorError("OpenAI API key not provided. Set OPENAI_API_KEY environment variable.")

        model = self.model or os.getenv("BLISS_OPENAI_MODEL") or "gpt-3.5-turbo"
        self.model = model
        self._openai = openai
        self._openai.api_key = api_key

    def _initialise_huggingface(self) -> None:
        try:
            from transformers import pipeline  # type: ignore[import-not-found]
        except ImportError as exc:
            raise ContentGeneratorError(
                "transformers package is required for provider 'huggingface'."
            ) from exc

        model = self.model or self.huggingface_model or os.getenv("BLISS_HF_MODEL") or "gpt2"
        self.model = model
        pipeline_kwargs: Dict[str, Any] = {}
        device = self.device or os.getenv("BLISS_HF_DEVICE")
        if device:
            pipeline_kwargs["device"] = device
        self._hf_pipeline = pipeline("text-generation", model=model, **pipeline_kwargs)

    # ──────────────────────────────────────────────────────────────────
    # Public API
    # ──────────────────────────────────────────────────────────────────

    def generate(self, prompt: str, *, system_prompt: Optional[str] = None) -> str:
        if self.provider == "openai":
            return self._generate_openai(prompt, system_prompt=system_prompt)
        return self._generate_huggingface(prompt, system_prompt=system_prompt)

    def _generate_openai(self, prompt: str, *, system_prompt: Optional[str]) -> str:
        messages: List[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        params: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        if self.temperature is not None:
            params["temperature"] = self.temperature
        if self.max_tokens is not None:
            params["max_tokens"] = self.max_tokens
        params.update(dict(self.extra_options))

        response = self._openai.ChatCompletion.create(**params)
        try:
            content = response["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise ContentGeneratorError("Unexpected response structure from OpenAI ChatCompletion") from exc
        if not isinstance(content, str):
            raise ContentGeneratorError("OpenAI returned non-string message content")
        return content.strip()

    def _generate_huggingface(self, prompt: str, *, system_prompt: Optional[str]) -> str:
        combined_prompt = prompt
        if system_prompt:
            combined_prompt = f"{system_prompt.strip()}\n{prompt}"

        generation_kwargs: Dict[str, Any] = dict(self.extra_options)
        if self.temperature is not None:
            generation_kwargs.setdefault("temperature", self.temperature)
        if self.max_tokens is not None:
            generation_kwargs.setdefault("max_new_tokens", self.max_tokens)

        outputs = self._hf_pipeline(combined_prompt, **generation_kwargs)
        if not outputs:
            raise ContentGeneratorError("Hugging Face pipeline did not return any output")

        first = outputs[0]
        if isinstance(first, Mapping):
            for key in ("generated_text", "summary_text", "text"):
                value = first.get(key)
                if isinstance(value, str):
                    return value.strip()
        raise ContentGeneratorError("Unsupported output format from Hugging Face pipeline")


class ADBClient:
    """Convenience wrapper around ``adb`` with optional serial selection."""

    def __init__(
        self,
        adb_path: Optional[str] = None,
        serial: Optional[str] = None,
        connect_address: Optional[str] = None,
        default_timeout: int = 60,
    ) -> None:
        self.adb_path = adb_path or os.getenv("ADB_BINARY", "adb")
        self.serial = serial or os.getenv("BLISS_ADB_SERIAL")
        self.connect_address = connect_address or os.getenv("BLISS_ADB_ADDRESS")
        self.default_timeout = default_timeout

    # ──────────────────────────────────────────────────────────────────────
    # Utility helpers
    # ──────────────────────────────────────────────────────────────────────

    def _adb_base(self) -> List[str]:
        cmd = [self.adb_path]
        if self.serial:
            cmd.extend(["-s", self.serial])
        return cmd

    def run(
        self,
        args: Sequence[str],
        *,
        timeout: Optional[int] = None,
        check: bool = True,
        capture_output: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        command = self._adb_base() + list(args)
        completed = subprocess.run(
            command,
            check=False,
            capture_output=capture_output,
            text=True,
            timeout=timeout or self.default_timeout,
        )
        if check and completed.returncode != 0:
            raise ADBCommandError(command, completed.returncode, completed.stdout, completed.stderr)
        return completed

    # ──────────────────────────────────────────────────────────────────────
    # Device discovery and connection management
    # ──────────────────────────────────────────────────────────────────────

    def connect(self, address: Optional[str] = None, *, timeout: Optional[int] = None) -> str:
        target = address or self.connect_address
        if not target:
            raise ValueError("No BlissOS address specified for adb connect")
        result = self.run(["connect", target], timeout=timeout, check=True)
        return result.stdout.strip()

    def disconnect(self, address: Optional[str] = None, *, timeout: Optional[int] = None, all_devices: bool = False) -> str:
        args = ["disconnect"]
        if all_devices:
            args.append("--all")
        elif address or self.connect_address:
            args.append(address or self.connect_address)  # type: ignore[arg-type]
        result = self.run(args, timeout=timeout, check=True)
        return result.stdout.strip()

    def list_devices(self) -> List[Dict[str, str]]:
        result = self.run(["devices", "-l"], timeout=15)
        devices: List[Dict[str, str]] = []
        for line in result.stdout.splitlines()[1:]:  # Skip the header
            line = line.strip()
            if not line:
                continue
            parts = line.split()
            serial = parts[0]
            status = parts[1] if len(parts) > 1 else "unknown"
            attrs: Dict[str, str] = {"serial": serial, "status": status}
            for token in parts[2:]:
                if ":" in token:
                    key, value = token.split(":", 1)
                    attrs[key] = value
            devices.append(attrs)
        return devices

    # ──────────────────────────────────────────────────────────────────────
    # App management helpers
    # ──────────────────────────────────────────────────────────────────────

    def install(self, apk_path: Path, *, reinstall: bool = False, timeout: Optional[int] = None) -> str:
        args = ["install"]
        if reinstall:
            args.append("-r")
        args.append(str(apk_path))
        result = self.run(args, timeout=timeout, check=True)
        return result.stdout.strip()

    def uninstall(self, package: str, *, keep_data: bool = False, timeout: Optional[int] = None) -> str:
        args = ["uninstall"]
        if keep_data:
            args.append("-k")
        args.append(package)
        result = self.run(args, timeout=timeout, check=True)
        return result.stdout.strip()

    def is_package_installed(self, package: str) -> bool:
        result = self.run(["shell", "pm", "path", package], check=False)
        return result.returncode == 0 and "package:" in result.stdout

    def force_stop(self, package: str) -> None:
        self.run(["shell", "am", "force-stop", package], check=False)

    def launch_activity(self, component: str, *, extras: Sequence[str] = ()) -> str:
        args = ["shell", "am", "start", "-n", component, *extras]
        result = self.run(args, check=True)
        return result.stdout.strip()

    def launch_via_monkey(self, package: str) -> str:
        args = ["shell", "monkey", "-p", package, "-c", "android.intent.category.LAUNCHER", "1"]
        result = self.run(args, check=True)
        return result.stdout.strip()

    def push(self, source: Path, destination: str) -> str:
        result = self.run(["push", str(source), destination], timeout=120)
        return result.stdout.strip()

    def wait_for_device(self, serial: Optional[str] = None, *, timeout: Optional[int] = None) -> None:
        args = []
        if serial or self.serial or self.connect_address:
            args.extend(["-s", serial or self.serial or self.connect_address])
        self.run(args + ["wait-for-device"], timeout=timeout or 60)

    def shell(self, *args: str, timeout: Optional[int] = None) -> str:
        result = self.run(["shell", *args], timeout=timeout or 30)
        return result.stdout.strip()


def _encode_input_text(value: str) -> str:
    """Prepare text for ``adb shell input text`` (escape spaces and quotes)."""

    escaped = value.replace(" ", "%s").replace("\n", "%n")
    escaped = escaped.replace("\t", "%t").replace("'", "\\'")
    return escaped


@dataclass
class ShareIntent:
    app: SocialAppConfig
    text: Optional[str] = None
    subject: Optional[str] = None
    media_files: Sequence[Path] = ()
    remote_directory: str = "/sdcard/Download"
    mime_type: Optional[str] = None
    share_activity: Optional[str] = None
    extras: MutableMapping[str, str] = field(default_factory=dict)

    def determine_mime(self) -> str:
        if self.mime_type:
            return self.mime_type
        if self.media_files:
            # Pick a heuristic based on file suffix.
            suffixes = {path.suffix.lower() for path in self.media_files if path.suffix}
            if suffixes <= {".jpg", ".jpeg", ".png", ".gif", ".webp"}:
                return "image/*"
            if suffixes <= {".mp4", ".mov", ".mkv", ".webm"}:
                return "video/*"
        return self.app.default_mime_type


class BlissSocialAutomation:
    """High level orchestration helpers for BlissOS social publishing."""

    def __init__(self, adb: Optional[ADBClient] = None) -> None:
        self.adb = adb or ADBClient()

    # ──────────────────────────────────────────────────────────────────────
    # App lifecycle operations
    # ──────────────────────────────────────────────────────────────────────

    def ensure_device(self) -> None:
        """Connect to the target BlissOS device if requested."""

        if self.adb.connect_address:
            self.adb.connect()
            # ``adb connect`` may change the default serial identifier.
            if not self.adb.serial:
                self.adb.serial = self.adb.connect_address
            self.adb.wait_for_device()

    def install_app(self, apk_path: Path, *, reinstall: bool = False) -> str:
        self.ensure_device()
        return self.adb.install(apk_path, reinstall=reinstall)

    def uninstall_app(self, package: str, *, keep_data: bool = False) -> str:
        self.ensure_device()
        return self.adb.uninstall(package, keep_data=keep_data)

    def ensure_app_installed(self, app: SocialAppConfig, apk_path: Optional[Path] = None) -> None:
        self.ensure_device()
        if self.adb.is_package_installed(app.package):
            return
        if not apk_path:
            raise RuntimeError(
                f"Application '{app.package}' is not installed and no APK path was provided."
            )
        self.install_app(apk_path, reinstall=False)

    # ──────────────────────────────────────────────────────────────────────
    # Post publishing helpers
    # ──────────────────────────────────────────────────────────────────────

    def launch_app(self, app: SocialAppConfig, *, activity: Optional[str] = None) -> str:
        self.ensure_device()
        component = app.component(activity)
        if component:
            return self.adb.launch_activity(component)
        return self.adb.launch_via_monkey(app.package)

    def force_stop(self, app: SocialAppConfig) -> None:
        self.ensure_device()
        self.adb.force_stop(app.package)

    def _prepare_remote_media(self, intent: ShareIntent) -> List[str]:
        remote_uris: List[str] = []
        for media in intent.media_files:
            destination = f"{intent.remote_directory.rstrip('/')}/{media.name}"
            self.adb.push(media, destination)
            remote_uris.append(f"file://{destination}")
        return remote_uris

    def push_assets(
        self,
        files: Sequence[Path],
        remote_directory: str = "/sdcard/Download",
    ) -> Dict[str, str]:
        """Push local files to the BlissOS device and return their destinations.

        The helper mirrors the behaviour of ``adb push`` but adds a few quality
        of life features required by social media automation scripts.  Each
        source path is resolved, validated and copied into ``remote_directory``
        while preserving the original filename.  The resulting dictionary maps
        the absolute local path to the computed remote location so the caller
        can later reference the uploaded assets when constructing share intents
        or other automation actions.
        """

        self.ensure_device()

        base_directory = remote_directory.rstrip("/") or "/"
        uploaded: Dict[str, str] = {}
        for item in files:
            path = Path(item)
            if not path.exists():
                raise FileNotFoundError(path)

            if base_directory == "/":
                destination = f"/{path.name}"
            else:
                destination = f"{base_directory}/{path.name}"

            self.adb.push(path, destination)
            uploaded[str(path.resolve())] = destination

        return uploaded

    def _build_share_command(self, intent: ShareIntent, remote_uris: Sequence[str]) -> List[str]:
        mime = intent.determine_mime()
        action = intent.app.share_action
        extras: List[str] = []
        if intent.subject:
            extras.extend(["-e", "android.intent.extra.SUBJECT", intent.subject])
        if intent.text:
            extras.extend(["-e", "android.intent.extra.TEXT", intent.text])
        for key, value in intent.extras.items():
            extras.extend(["-e", key, value])
        if remote_uris:
            if intent.app.supports_multiple and len(remote_uris) > 1:
                action = "android.intent.action.SEND_MULTIPLE"
            for uri in remote_uris:
                extras.extend(["--eu", "android.intent.extra.STREAM", uri])
        extras.extend(intent.app.extra_flags)
        component = intent.app.share_component(intent.share_activity)
        command = ["shell", "am", "start", "-a", action, "-t", mime]
        if component:
            command.extend(["-n", component])
        command.extend(extras)
        return command

    def share(self, intent: ShareIntent) -> str:
        self.ensure_device()
        remote_uris = self._prepare_remote_media(intent)
        command = self._build_share_command(intent, remote_uris)
        result = self.adb.run(command, timeout=120)
        return result.stdout.strip()

    def generate_post_text(
        self,
        prompt: str,
        *,
        generator: Optional[ContentGenerator] = None,
        generator_options: Optional[Mapping[str, Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> str:
        """Generate social media copy using the configured LLM provider."""

        if generator is not None and generator_options is not None:
            raise ValueError("Specify either an existing generator or generator_options, not both")

        if generator is None:
            options_dict = dict(generator_options or {})
            generator = ContentGenerator(**options_dict)

        return generator.generate(prompt, system_prompt=system_prompt)

    def input_text(self, value: str) -> str:
        self.ensure_device()
        encoded = _encode_input_text(value)
        result = self.adb.shell("input", "text", encoded)
        return result.strip()

    def tap(self, x: int, y: int) -> str:
        self.ensure_device()
        result = self.adb.shell("input", "tap", str(x), str(y))
        return result.strip()

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> str:
        self.ensure_device()
        result = self.adb.shell(
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration_ms),
        )
        return result.strip()

    def publish_post(
        self,
        app_name: str,
        *,
        text: Optional[str] = None,
        generation_prompt: Optional[str] = None,
        system_prompt: Optional[str] = None,
        generator: Optional[ContentGenerator] = None,
        generator_options: Optional[Mapping[str, Any]] = None,
        subject: Optional[str] = None,
        media: Sequence[Path] = (),
        remote_directory: str = "/sdcard/Download",
        share_activity: Optional[str] = None,
        extras: Optional[MutableMapping[str, str]] = None,
    ) -> str:
        try:
            app = SOCIAL_APPS[app_name.lower()]
        except KeyError as exc:
            raise KeyError(f"Unknown social app '{app_name}'. Available: {', '.join(SOCIAL_APPS)}") from exc

        resolved_text = text
        if generation_prompt and resolved_text is None:
            resolved_text = self.generate_post_text(
                generation_prompt,
                generator=generator,
                generator_options=generator_options,
                system_prompt=system_prompt,
            )

        intent = ShareIntent(
            app=app,
            text=resolved_text,
            subject=subject,
            media_files=list(media),
            remote_directory=remote_directory,
            share_activity=share_activity,
            extras=extras or {},
        )
        return self.share(intent)

    def publish_batch(
        self,
        plans: Sequence[Mapping[str, Any]],
        *,
        stop_on_error: bool = False,
    ) -> List[Dict[str, Any]]:
        """Execute multiple :meth:`publish_post` jobs sequentially."""

        results: List[Dict[str, Any]] = []
        for index, plan in enumerate(plans):
            if "app" not in plan:
                raise KeyError(f"Batch entry {index} is missing the 'app' field")

            app_name = str(plan["app"])
            text = plan.get("text")
            subject = plan.get("subject")
            remote_dir = str(plan.get("remote_directory", "/sdcard/Download"))
            share_activity = plan.get("share_activity")

            extras_obj = plan.get("extras") or {}
            if not isinstance(extras_obj, Mapping):
                raise TypeError(
                    f"Batch entry {index} extras must be a mapping, got {type(extras_obj)!r}"
                )
            extras: Dict[str, str] = {str(k): str(v) for k, v in extras_obj.items()}

            media_obj = plan.get("media") or []
            if isinstance(media_obj, (str, Path)):
                media_iterable: Iterable[Any] = [media_obj]
            else:
                if not isinstance(media_obj, Iterable):
                    raise TypeError(
                        f"Batch entry {index} media must be iterable or string, got {type(media_obj)!r}"
                    )
                media_iterable = media_obj
            media_paths = [Path(str(item)) for item in media_iterable]

            try:
                output = self.publish_post(
                    app_name,
                    text=text if text is None or isinstance(text, str) else str(text),
                    subject=subject if subject is None or isinstance(subject, str) else str(subject),
                    media=media_paths,
                    remote_directory=remote_dir,
                    share_activity=share_activity if isinstance(share_activity, str) else None,
                    extras=extras,
                )
                results.append({"app": app_name, "status": "ok", "output": output})
            except Exception as exc:  # pragma: no cover - error path validated separately
                results.append({"app": app_name, "status": "error", "error": str(exc)})
                if stop_on_error:
                    raise
        return results


# ──────────────────────────────────────────────────────────────────────────
# Command line interface
# ──────────────────────────────────────────────────────────────────────────


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Automate BlissOS social media publishing via adb",
    )
    parser.add_argument("--adb", dest="adb_path", help="Path to the adb binary")
    parser.add_argument("--serial", dest="serial", help="ADB serial/host of the BlissOS VM")
    parser.add_argument("--connect", dest="connect", help="Connect to BlissOS before running commands")
    parser.add_argument(
        "--timeout",
        type=int,
        default=60,
        help="Default adb command timeout in seconds",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("devices", help="List connected adb devices")

    connect_parser = subparsers.add_parser("connect", help="Run adb connect against the BlissOS VM")
    connect_parser.add_argument("address", nargs="?", help="host:port pair")

    disconnect_parser = subparsers.add_parser("disconnect", help="Run adb disconnect")
    disconnect_parser.add_argument("address", nargs="?")
    disconnect_parser.add_argument("--all", action="store_true", help="Disconnect all adb sessions")

    install_parser = subparsers.add_parser("install", help="Install an APK onto BlissOS")
    install_parser.add_argument("apk", type=Path)
    install_parser.add_argument("--reinstall", action="store_true", help="Use adb install -r")

    uninstall_parser = subparsers.add_parser("uninstall", help="Uninstall an application from BlissOS")
    uninstall_parser.add_argument("package")
    uninstall_parser.add_argument("--keep-data", action="store_true", help="Keep data/cache via adb uninstall -k")

    launch_parser = subparsers.add_parser("launch", help="Launch a supported social media app")
    launch_parser.add_argument("app", choices=sorted(SOCIAL_APPS))
    launch_parser.add_argument("--activity", help="Override the activity to launch")

    share_parser = subparsers.add_parser("share", help="Trigger a share intent for a social media app")
    share_parser.add_argument("app", choices=sorted(SOCIAL_APPS))
    share_parser.add_argument("--text", help="Text of the post")
    share_parser.add_argument("--subject", help="Subject/title for compatible apps")
    share_parser.add_argument("--media", nargs="*", type=Path, default=[], help="Local media files to upload")
    share_parser.add_argument(
        "--remote-dir",
        default="/sdcard/Download",
        help="Directory on the device where media files will be pushed",
    )
    share_parser.add_argument("--share-activity", help="Override the share activity component")
    share_parser.add_argument(
        "--extra",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Additional extras to include in the intent",
    )
    share_parser.add_argument(
        "--prompt",
        dest="generation_prompt",
        help="Prompt used to generate the post text via the configured LLM",
    )
    share_parser.add_argument(
        "--system-prompt",
        dest="system_prompt",
        help="Optional system prompt for chat-based generators",
    )
    share_parser.add_argument(
        "--llm-provider",
        choices=["openai", "huggingface"],
        dest="llm_provider",
        help="Override the LLM provider when generating text",
    )
    share_parser.add_argument(
        "--llm-model",
        dest="llm_model",
        help="Model identifier for the selected LLM provider",
    )
    share_parser.add_argument(
        "--llm-temperature",
        dest="llm_temperature",
        type=float,
        help="Sampling temperature to use for the generator",
    )
    share_parser.add_argument(
        "--llm-max-tokens",
        dest="llm_max_tokens",
        type=int,
        help="Maximum number of tokens (or new tokens) to generate",
    )

    push_parser = subparsers.add_parser(
        "push",
        help="Copy local files into BlissOS storage",
    )
    push_parser.add_argument("files", nargs="+", type=Path, help="Local files to upload")
    push_parser.add_argument(
        "--remote-dir",
        default="/sdcard/Download",
        help="Directory on the device where files will be stored",
    )

    batch_share_parser = subparsers.add_parser(
        "batch-share",
        help="Execute multiple share actions defined in a JSON plan",
    )
    batch_share_parser.add_argument("plan", type=Path, help="Path to the batch JSON file")
    batch_share_parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help="Abort batch execution when a share action fails",
    )

    input_parser = subparsers.add_parser("input-text", help="Send text using adb shell input text")
    input_parser.add_argument("text")

    tap_parser = subparsers.add_parser("tap", help="Simulate a touchscreen tap")
    tap_parser.add_argument("x", type=int)
    tap_parser.add_argument("y", type=int)

    swipe_parser = subparsers.add_parser("swipe", help="Simulate a swipe gesture")
    swipe_parser.add_argument("x1", type=int)
    swipe_parser.add_argument("y1", type=int)
    swipe_parser.add_argument("x2", type=int)
    swipe_parser.add_argument("y2", type=int)
    swipe_parser.add_argument("--duration", type=int, default=300, help="Swipe duration in milliseconds")

    generate_parser = subparsers.add_parser(
        "generate",
        help="Generate social media post text using the configured LLM",
    )
    generate_parser.add_argument("prompt", help="Prompt to feed into the generator")
    generate_parser.add_argument(
        "--system-prompt",
        dest="system_prompt",
        help="Optional system prompt for the generator",
    )
    generate_parser.add_argument(
        "--llm-provider",
        choices=["openai", "huggingface"],
        dest="llm_provider",
        help="Override the default LLM provider",
    )
    generate_parser.add_argument(
        "--llm-model",
        dest="llm_model",
        help="Model identifier to use for generation",
    )
    generate_parser.add_argument(
        "--llm-temperature",
        dest="llm_temperature",
        type=float,
        help="Sampling temperature for the generator",
    )
    generate_parser.add_argument(
        "--llm-max-tokens",
        dest="llm_max_tokens",
        type=int,
        help="Maximum number of tokens (or new tokens) to produce",
    )

    return parser


def _extras_from_pairs(pairs: Iterable[str]) -> Dict[str, str]:
    extras: Dict[str, str] = {}
    for item in pairs:
        if "=" not in item:
            raise ValueError(f"Invalid extra '{item}'. Expected KEY=VALUE format.")
        key, value = item.split("=", 1)
        extras[key] = value
    return extras


def _generator_options_from_args(options: Any) -> Dict[str, Any]:
    config: Dict[str, Any] = {}
    provider = getattr(options, "llm_provider", None)
    if provider:
        config["provider"] = provider
    model = getattr(options, "llm_model", None)
    if model:
        config["model"] = model
    temperature = getattr(options, "llm_temperature", None)
    if temperature is not None:
        config["temperature"] = temperature
    max_tokens = getattr(options, "llm_max_tokens", None)
    if max_tokens is not None:
        config["max_tokens"] = max_tokens
    return config


def _load_batch_plan(path: Path) -> List[MutableMapping[str, Any]]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # pragma: no cover - invalid JSON path unlikely
        raise ValueError(f"Failed to parse batch plan '{path}': {exc}") from exc

    if isinstance(data, list):
        return [dict(entry) for entry in data]

    if isinstance(data, dict):
        posts = data.get("posts")
        if isinstance(posts, list):
            return [dict(entry) for entry in posts]

    raise ValueError(
        "Batch plan must be a list of entries or an object with a 'posts' list"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    options = parser.parse_args(argv)

    adb_client = ADBClient(
        adb_path=options.adb_path,
        serial=options.serial,
        connect_address=options.connect,
        default_timeout=options.timeout,
    )
    automation = BlissSocialAutomation(adb_client)

    try:
        if options.command == "devices":
            devices = automation.adb.list_devices()
            print(json.dumps(devices, indent=2))
            return 0

        if options.command == "connect":
            output = automation.adb.connect(options.address)
            print(output)
            return 0

        if options.command == "disconnect":
            output = automation.adb.disconnect(options.address, all_devices=options.all)
            print(output)
            return 0

        if options.command == "install":
            output = automation.install_app(options.apk, reinstall=options.reinstall)
            print(output)
            return 0

        if options.command == "uninstall":
            output = automation.uninstall_app(options.package, keep_data=options.keep_data)
            print(output)
            return 0

        if options.command == "launch":
            app = SOCIAL_APPS[options.app]
            output = automation.launch_app(app, activity=options.activity)
            print(output)
            return 0

        if options.command == "generate":
            generator_options = _generator_options_from_args(options)
            text = automation.generate_post_text(
                options.prompt,
                generator_options=generator_options or None,
                system_prompt=options.system_prompt,
            )
            print(text)
            return 0

        if options.command == "share":
            extras = _extras_from_pairs(options.extra)
            media_paths = [Path(p) for p in options.media]
            generator_options = _generator_options_from_args(options)
            output = automation.publish_post(
                options.app,
                text=options.text,
                generation_prompt=options.generation_prompt,
                system_prompt=options.system_prompt,
                generator_options=generator_options or None,
                subject=options.subject,
                media=media_paths,
                remote_directory=options.remote_dir,
                share_activity=options.share_activity,
                extras=extras,
            )
            print(output)
            return 0

        if options.command == "push":
            uploads = automation.push_assets(options.files, remote_directory=options.remote_dir)
            print(json.dumps(uploads, indent=2))
            return 0

        if options.command == "batch-share":
            plans = _load_batch_plan(options.plan)
            results = automation.publish_batch(plans, stop_on_error=options.stop_on_error)
            print(json.dumps(results, indent=2))
            return 0

        if options.command == "input-text":
            print(automation.input_text(options.text))
            return 0

        if options.command == "tap":
            print(automation.tap(options.x, options.y))
            return 0

        if options.command == "swipe":
            print(automation.swipe(options.x1, options.y1, options.x2, options.y2, options.duration))
            return 0

    except (ADBCommandError, subprocess.SubprocessError, ValueError, RuntimeError) as exc:
        print(str(exc), file=sys.stderr)
        return 1

    parser.print_help()
    return 1


if __name__ == "__main__":  # pragma: no cover - manual execution entry-point
    raise SystemExit(main())
