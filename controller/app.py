import os
import json
import shlex
import logging
import subprocess
import socket
from io import StringIO
from functools import lru_cache
from typing import Optional, Dict, Any, List, Tuple, Literal, ClassVar
import re
from string import Template

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator, IPvAnyNetwork, IPvAnyAddress
from proxmoxer import ProxmoxAPI
import paramiko

from agent_profile import get_agent_profile

# ─────────────────────────────────────────────
# Логування
# ─────────────────────────────────────────────
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=LOG_LEVEL,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
log = logging.getLogger("universal-controller")

# ─────────────────────────────────────────────
# FastAPI
# ─────────────────────────────────────────────
app = FastAPI(title="Universal LLM Controller", version="2.0.0")

# CORS (наприклад, якщо викликаєш з OpenWebUI з іншого походження)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in os.getenv("CORS_ALLOW_ORIGINS", "*").split(",")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# Agent profile metadata
# ─────────────────────────────────────────────


@app.get("/agent/profile")
def agent_profile() -> Dict[str, Any]:
    """Return the static configuration used to prime the LLM agent."""

    try:
        return get_agent_profile()
    except Exception as exc:  # pragma: no cover - defensive guard
        raise _http_500(f"Failed to load agent profile: {exc}") from exc

# ─────────────────────────────────────────────
# Моделі запитів
# ─────────────────────────────────────────────
class StartStopReq(BaseModel):
    node: Optional[str] = None  # може бути не заданий
    vmid: int


class CreateLXCReq(BaseModel):
    node: Optional[str] = None
    vmid: int
    hostname: str
    cores: int = 2
    memory: int = 2048  # MB
    storage: str
    rootfs_gb: int = 16
    bridge: str = "vmbr0"
    ip_cidr: Optional[IPvAnyNetwork] = None  # напр. "192.168.1.150/24"
    gateway: Optional[IPvAnyAddress] = None
    ssh_public_key: Optional[str] = None
    password: Optional[str] = None      # тимчасовий пароль root у контейнері
    unprivileged: bool = True
    features: Optional[Dict[str, int]] = None  # напр. {"nesting":1,"keyctl":1}
    ostemplate: str                        # "local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst"
    start: bool = True

    @field_validator("cores")
    @classmethod
    def _min_cores(cls, v: int) -> int:
        if v < 1:
            raise ValueError("cores must be >= 1")
        return v

    @field_validator("memory")
    @classmethod
    def _min_memory(cls, v: int) -> int:
        if v < 128:
            raise ValueError("memory must be >= 128 MB")
        return v

    @field_validator("rootfs_gb")
    @classmethod
    def _min_rootfs(cls, v: int) -> int:
        if v < 4:
            raise ValueError("rootfs_gb must be >= 4 GB")
        return v


class LXCExecSpec(BaseModel):
    vmid: int
    cmd: Optional[str] = None
    commands: Optional[List[str]] = None

    _allowed_executables: ClassVar[Tuple[str, ...]] = (
        "systemctl", "service", "journalctl", "ls", "cat", "tail",
        "head", "df", "du", "ps", "kill", "docker", "git",
        "curl", "wget", "python3", "pip", "bash", "sh",
        "apt", "apt-get"
    )

    @classmethod
    def _validate_command(cls, raw: str) -> str:
        command = raw.strip()
        if not command:
            raise ValueError("Command cannot be empty")
        if any(c in command for c in [";", "|", "`"]):
            raise ValueError("Shell metacharacters are not allowed")
        if re.search(r"(?<!&)&(?!&)", command):
            raise ValueError("Shell metacharacters are not allowed")

        try:
            tokens = shlex.split(command)
        except ValueError as e:
            raise ValueError(f"Invalid command: {e}") from e

        if not tokens:
            raise ValueError("Command cannot be empty")

        segments: List[List[str]] = []
        current: List[str] = []
        for token in tokens:
            if "&&" in token and token != "&&":
                raise ValueError("Use spaces around '&&' to chain commands")
            if token == "&&":
                if not current:
                    raise ValueError("Command segment cannot be empty before '&&'")
                segments.append(current)
                current = []
                continue
            current.append(token)

        if not current:
            raise ValueError("Command cannot end with '&&'")

        segments.append(current)

        for segment in segments:
            executable = os.path.basename(segment[0])
            if executable not in cls._allowed_executables:
                allowed = list(cls._allowed_executables)
                raise ValueError(f"Command not allowed. Allowed executables: {allowed}")

        return command

    @field_validator("cmd")
    @classmethod
    def validate_cmd(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return None
        return cls._validate_command(v)

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, v: Optional[List[str]]) -> Optional[List[str]]:
        if v is None:
            return None
        if not v:
            raise ValueError("commands cannot be empty")
        return [cls._validate_command(cmd) for cmd in v]

    @model_validator(mode="after")
    def check_payload(self):
        if not self.cmd and not self.commands:
            raise ValueError("Either 'cmd' or 'commands' must be provided")
        if self.cmd and self.commands:
            raise ValueError("Use either 'cmd' or 'commands', not both")
        return self


class DeploySpec(BaseModel):
    target_vmid: int
    repo_url: str
    workdir: str = "/opt/app"
    setup: List[str] = Field(default_factory=lambda: [
        "apt-get update",
        "apt-get install -y git curl python3 python3-venv"
    ])
    commands: List[str] = Field(default_factory=lambda: [
        "git clone {{repo_url}} {{workdir}} || (rm -rf {{workdir}} && git clone {{repo_url}} {{workdir}})",
        "cd {{workdir}} && if [ -f requirements.txt ]; then python3 -m venv .venv && . .venv/bin/activate && pip install -U pip -r requirements.txt; fi",
        "cd {{workdir}} && if [ -f docker-compose.yml ]; then curl -fsSL https://get.docker.com | sh && systemctl start docker && docker compose up -d; fi",
        "cd {{workdir}} && if [ -f Makefile ]; then make run || true; fi"
    ])


class SSHSpec(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "description": (
                "Параметри SSH-з'єднання. Вкажіть один з методів автентифікації: "
                "password, key_path або key_data_b64. Якщо жоден спосіб не задано, "
                "контролер використає попередньо налаштований ключ (наприклад, з ENV "
                "чи /keys/pve_id_rsa)."
            ),
            "anyOf": [
                {"required": ["password"]},
                {"required": ["key_path"]},
                {"required": ["key_data_b64"]},
            ],
        }
    )

    host: Optional[str] = Field(
        default=None,
        description=(
            "SSH host або user@host. Підтримуються DNS-імена, IPv4/IPv6 (у форматі [addr]), "
            "необов'язковий :port та URI ssh://user@host:port. Якщо не задано, береться з ENV "
            "DEFAULT_SSH_HOST/PVE_SSH_HOST."
        ),
    )
    user: Optional[str] = Field(
        default=None,
        description=(
            "SSH-користувач. Коли host вже містить user@..., пріоритет має він. Якщо поле не задано, "
            "використовується DEFAULT_SSH_USER або PVE_SSH_USER (типово root)."
        ),
    )
    port: Optional[int] = Field(
        default=None,
        description=(
            "SSH-порт для випадків, коли host не містить власного значення. Якщо не задано, "
            "використовується 22 або значення з ENV DEFAULT_SSH_PORT/PVE_SSH_PORT."
        ),
    )
    cmd: str
    key_path: Optional[str] = Field(
        default=None,
        description=(
            "Шлях до приватного ключа на контролері (наприклад, /keys/pve_id_rsa). Якщо пропустити, "
            "використається DEFAULT_SSH_KEY_PATH/PVE_SSH_KEY_PATH або інший попередньо налаштований ключ."
        ),
    )
    key_data_b64: Optional[str] = Field(
        default=None,
        description=(
            "Base64(OpenSSH private key) для одноразового передавання ключа. "
            "Якщо пропустити, контролер спробує використати попередньо налаштований ключ."
        ),
    )
    password: Optional[str] = Field(
        default=None,
        description=(
            "Пароль для SSH-автентифікації. Якщо пропустити та не вказано ключ, контролер спробує використати попередньо "
            "налаштований ключ."
        ),
    )
    strict_host_key: Optional[bool] = Field(
        default=None,
        description="Увімкнути перевірку ключа хоста (StrictHostKeyChecking). За замовчуванням значення з ENV DEFAULT_SSH_STRICT_HOST_KEY.",
    )
    env: Optional[Dict[str, str]] = Field(
        default=None,
        description="Додаткові змінні середовища, які потрібно експортувати перед виконанням команди.",
    )
    cwd: Optional[str] = Field(
        default=None,
        description="Робоча директорія на віддаленій машині, в якій виконуватиметься команда.",
    )


class AppLaunchSpec(BaseModel):
    host: Optional[str] = None
    user: Optional[str] = None
    port: Optional[int] = None
    key_path: Optional[str] = None
    key_data_b64: Optional[str] = None
    password: Optional[str] = None
    strict_host_key: Optional[bool] = None
    program: str = Field(..., description="firefox | google-chrome | chromium | code | xterm | tmux | bash ...")
    args: List[str] = Field(default_factory=list)
    env: Optional[Dict[str, str]] = None
    cwd: Optional[str] = None
    background: bool = True
    display: Optional[str] = Field(default=None, description="Напр., ':0' для GUI X11 хоста")


class BrowserSpec(BaseModel):
    host: Optional[str] = None
    user: Optional[str] = None
    port: Optional[int] = None
    key_path: Optional[str] = None
    key_data_b64: Optional[str] = None
    password: Optional[str] = None
    strict_host_key: Optional[bool] = None

    action: Literal["open", "screenshot", "pdf"] = "open"
    url: str
    headless: bool = True
    browser_cmds: List[str] = Field(default_factory=lambda: ["google-chrome", "chromium-browser", "chromium"])
    window_size: str = "1280,800"
    user_data_dir: Optional[str] = None
    output_path: Optional[str] = Field(default=None, description="для screenshot/pdf на віддаленій машині")
    extra_args: List[str] = Field(default_factory=list)

# ─────────────────────────────────────────────
# Хелпери
# ─────────────────────────────────────────────
def _http_500(detail: str) -> HTTPException:
    log.exception(detail)
    return HTTPException(status_code=500, detail=detail)


def _bool_env(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _resolve_proxmox_user(
    raw_user: Optional[str], realm: str, raw_token_name: Optional[str]
) -> Tuple[str, Optional[str]]:
    """Normalize Proxmox user and token name values.

    Supports the ``user@realm!token`` syntax that Proxmox uses for API tokens.
    If a token name is embedded after ``!`` and ``PROXMOX_TOKEN_NAME`` is not
    provided explicitly, the token name is derived automatically.
    """

    user = (raw_user or "").strip()
    token_name_env = (raw_token_name or "").strip() or None

    if not user:
        user = f"root@{realm}"

    embedded_token: Optional[str] = None
    if "!" in user:
        user_part, token_part = user.split("!", 1)
        user = user_part.strip()
        embedded_token = token_part.strip() or None
        if not user:
            raise RuntimeError(
                "Invalid PROXMOX_USER format: user part before '!' is empty."
            )

    token_name = token_name_env or embedded_token
    if embedded_token and not token_name_env:
        log.info("Derived PROXMOX_TOKEN_NAME='%s' from PROXMOX_USER", token_name)

    return user, token_name


@lru_cache(maxsize=1)
def get_proxmox() -> ProxmoxAPI:
    """
    Створює та кешує клієнт ProxmoxAPI.
    ENV:
      PROXMOX_HOST=192.168.1.140
      PROXMOX_PORT=8006
      PROXMOX_USER=root@pam!WebUI
      PROXMOX_TOKEN_NAME=...
      PROXMOX_TOKEN_VALUE=...
      PROXMOX_PASSWORD=... (не бажано)
      PROXMOX_VERIFY_SSL=false | VERIFY_SSL=false
    """
    host = os.getenv("PROXMOX_HOST")
    realm = os.getenv("PROXMOX_REALM", "pam")
    user, token_name = _resolve_proxmox_user(
        os.getenv("PROXMOX_USER"), realm, os.getenv("PROXMOX_TOKEN_NAME")
    )
    token_value = os.getenv("PROXMOX_TOKEN_VALUE")
    password = os.getenv("PROXMOX_PASSWORD")
    verify_ssl = _bool_env("PROXMOX_VERIFY_SSL", _bool_env("VERIFY_SSL", False))
    port = int(os.getenv("PROXMOX_PORT", "8006"))

    if not host:
        raise RuntimeError("Missing PROXMOX_HOST")

    kwargs: Dict[str, Any] = {
        "user": user,
        "verify_ssl": verify_ssl,
        "port": port,
        "backend": "https",
    }

    if token_name and token_value:
        kwargs["token_name"] = token_name
        kwargs["token_value"] = token_value
        log.info("Using Proxmox API token authentication.")
    elif password:
        kwargs["password"] = password
        log.warning("Using password auth (consider API token instead).")
    else:
        raise RuntimeError(
            "Provide either PROXMOX_TOKEN_NAME + PROXMOX_TOKEN_VALUE or PROXMOX_PASSWORD."
        )

    log.info("Connecting to Proxmox at https://%s:%s (verify_ssl=%s)", host, port, verify_ssl)
    return ProxmoxAPI(host, **kwargs)


def _default_node(prox: ProxmoxAPI, node: Optional[str]) -> str:
    if node:
        return node
    nodes = [n["node"] for n in prox.nodes.get()]
    if not nodes:
        raise HTTPException(500, "No Proxmox nodes available")
    return nodes[0]


def _require_pve_ssh() -> Tuple[str, str, str]:
    """Return host, user and key path for Proxmox SSH or raise HTTP 400."""
    host = os.getenv("PVE_SSH_HOST")
    user = os.getenv("PVE_SSH_USER")
    key = os.getenv("PVE_SSH_KEY_PATH", "/keys/pve_id_rsa")
    if not host:
        raise HTTPException(400, "PVE_SSH_HOST is not configured")
    if not user:
        raise HTTPException(400, "PVE_SSH_USER is not configured")
    if not os.path.exists(key):
        raise HTTPException(400, f"SSH key not found at {key}")
    return host, user, key


def _ssh_pct_list() -> List[Dict[str, Any]]:
    """
    Список LXC напряму з Proxmox-хоста через SSH:
      pct list --output-format json
    Потрібні ENV: PVE_SSH_HOST, PVE_SSH_USER, PVE_SSH_KEY_PATH.
    """
    host, user, key = _require_pve_ssh()
    cmd = ["ssh", "-i", key, f"{user}@{host}", "pct list --output-format json"]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    except Exception as e:
        raise RuntimeError(f"SSH/pct call failed: {e}")
    if res.returncode != 0:
        raise RuntimeError(f"pct list rc={res.returncode}: {res.stderr or res.stdout}")
    try:
        return json.loads(res.stdout)
    except Exception:
        raise RuntimeError(f"Unexpected pct output: {res.stdout!r}")


def _first_non_empty(*values: Any) -> Optional[Any]:
    for value in values:
        if value is None:
            continue
        if isinstance(value, str):
            stripped = value.strip()
            if not stripped:
                continue
            return stripped
        return value
    return None


def _env_non_empty(name: str) -> Optional[str]:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _resolve_ssh_connection(spec: BaseModel) -> Dict[str, Any]:
    host = _first_non_empty(
        getattr(spec, "host", None),
        _env_non_empty("DEFAULT_SSH_HOST"),
        _env_non_empty("PVE_SSH_HOST"),
    )
    if not host:
        raise HTTPException(
            400,
            "SSH host is not provided. Supply 'host' in the request or configure DEFAULT_SSH_HOST/PVE_SSH_HOST.",
        )

    user = _first_non_empty(
        getattr(spec, "user", None),
        _env_non_empty("DEFAULT_SSH_USER"),
        _env_non_empty("PVE_SSH_USER"),
        "root",
    )

    port_candidate = getattr(spec, "port", None)
    if port_candidate is None:
        port_candidate = _first_non_empty(
            _env_non_empty("DEFAULT_SSH_PORT"),
            _env_non_empty("PVE_SSH_PORT"),
        )
    if port_candidate is None:
        port = 22
    else:
        try:
            port = int(port_candidate)
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, f"Invalid SSH port value: {port_candidate}") from exc

    key_path = _first_non_empty(
        getattr(spec, "key_path", None),
        _env_non_empty("DEFAULT_SSH_KEY_PATH"),
        _env_non_empty("PVE_SSH_KEY_PATH"),
    )
    if not key_path and os.path.exists("/keys/pve_id_rsa"):
        key_path = "/keys/pve_id_rsa"

    key_data_b64 = _first_non_empty(
        getattr(spec, "key_data_b64", None),
        _env_non_empty("DEFAULT_SSH_KEY_B64"),
    )

    password = _first_non_empty(
        getattr(spec, "password", None),
        _env_non_empty("DEFAULT_SSH_PASSWORD"),
        _env_non_empty("PVE_SSH_PASSWORD"),
    )

    strict_host_key = getattr(spec, "strict_host_key", None)
    if strict_host_key is None:
        strict_host_key = _bool_env("DEFAULT_SSH_STRICT_HOST_KEY", False)

    return {
        "host": host,
        "user": user,
        "port": port,
        "key_path": key_path,
        "key_data_b64": key_data_b64,
        "password": password,
        "strict_host_key": bool(strict_host_key),
    }


# ─────────────────────────────────────────────
# SSH runner (universal)
# ─────────────────────────────────────────────
class SSHError(RuntimeError):
    pass


class SSHRunner:
    def __init__(
        self,
        host: str,
        user: str = "root",
        port: int = 22,
        key_path: Optional[str] = None,
        key_data_b64: Optional[str] = None,
        password: Optional[str] = None,
        strict_host_key: bool = False,
        timeout: int = 30,
    ):
        raw_host = host.strip() if host else ""
        if not raw_host:
            raise SSHError("SSH host cannot be empty")

        lowered = raw_host.lower()
        if lowered.startswith("ssh://"):
            raw_host = raw_host[6:]
        elif "://" in raw_host:
            scheme = raw_host.split("://", 1)[0]
            raise SSHError(f"Unsupported SSH URI scheme: {scheme}")

        raw_host = raw_host.strip()
        if not raw_host:
            raise SSHError("SSH host cannot be empty")

        if "/" in raw_host:
            host_only, remainder = raw_host.split("/", 1)
            if remainder.strip():
                raise SSHError("SSH host specification must not include a path component")
            raw_host = host_only.strip()

        detected_user: Optional[str] = None
        detected_port: Optional[int] = None

        if "@" in raw_host:
            user_part, raw_host = raw_host.split("@", 1)
            user_part = user_part.strip()
            raw_host = raw_host.strip()
            if not user_part:
                raise SSHError("SSH username in host specification cannot be empty")
            if not raw_host:
                raise SSHError("SSH host cannot be empty")
            detected_user = user_part

        if raw_host.startswith("["):
            end = raw_host.find("]")
            if end == -1:
                raise SSHError("Invalid IPv6 SSH host format. Expected closing ']'.")
            inner_host = raw_host[1:end].strip()
            if not inner_host:
                raise SSHError("SSH host cannot be empty")
            remainder = raw_host[end + 1 :]
            if remainder:
                if not remainder.startswith(":"):
                    raise SSHError("Invalid SSH host format after IPv6 literal")
                port_str = remainder[1:].strip()
                if not port_str:
                    raise SSHError("SSH port in host specification cannot be empty")
                try:
                    detected_port = int(port_str)
                except ValueError as exc:
                    raise SSHError(f"Invalid SSH port value: {port_str}") from exc
            parsed_host = inner_host
        elif ":" in raw_host and raw_host.count(":") == 1:
            host_part, port_str = raw_host.rsplit(":", 1)
            host_part = host_part.strip()
            port_str = port_str.strip()
            if not host_part:
                raise SSHError("SSH host cannot be empty")
            if not port_str:
                raise SSHError("SSH port in host specification cannot be empty")
            try:
                detected_port = int(port_str)
            except ValueError as exc:
                raise SSHError(f"Invalid SSH port value: {port_str}") from exc
            parsed_host = host_part
        else:
            parsed_host = raw_host

        if not parsed_host:
            raise SSHError("SSH host cannot be empty")

        final_port = detected_port if detected_port is not None else port
        try:
            final_port_int = int(final_port)
        except (TypeError, ValueError) as exc:
            raise SSHError(f"Invalid SSH port value: {final_port}") from exc
        if not (1 <= final_port_int <= 65535):
            raise SSHError("SSH port must be between 1 and 65535")

        final_user = detected_user if detected_user is not None else user
        final_user = final_user.strip() if final_user else ""
        if not final_user:
            raise SSHError("SSH username cannot be empty")

        self.host = parsed_host
        self.port = final_port_int
        self.user = final_user
        self.key_path = key_path
        self.key_data_b64 = key_data_b64
        self.password = password
        self.strict_host_key = strict_host_key
        self.timeout = timeout

    @staticmethod
    def _load_pkey_from_data(text: str) -> paramiko.PKey:
        excs = []
        for loader in (paramiko.Ed25519Key.from_private_key,
                       paramiko.RSAKey.from_private_key,
                       paramiko.ECDSAKey.from_private_key):
            try:
                return loader(file_obj=StringIO(text))
            except Exception as e:
                excs.append(e)
        raise SSHError(f"Unsupported private key (Ed25519/RSA/ECDSA). Errors: {excs}")

    @staticmethod
    def _load_pkey_from_path(path: str) -> paramiko.PKey:
        excs = []
        for loader in (paramiko.Ed25519Key.from_private_key_file,
                       paramiko.RSAKey.from_private_key_file,
                       paramiko.ECDSAKey.from_private_key_file):
            try:
                return loader(path)
            except Exception as e:
                excs.append(e)
        raise SSHError(f"Unsupported key at {path} (Ed25519/RSA/ECDSA). Errors: {excs}")

    def _get_pkey(self) -> Optional[paramiko.PKey]:
        if self.key_path:
            return self._load_pkey_from_path(self.key_path)
        if self.key_data_b64:
            try:
                text = json.loads(self.key_data_b64)  # якщо випадково передали JSON-рядок
            except Exception:
                text = self.key_data_b64
            try:
                # якщо це base64 (без BEGIN), декодуємо
                if "BEGIN " not in text:
                    import base64
                    text = base64.b64decode(text).decode("utf-8")
            except Exception:
                pass
            return self._load_pkey_from_data(text)
        return None

    def run(self, cmd: str, timeout: int = 900, env: Optional[Dict[str, str]] = None, cwd: Optional[str] = None) -> Tuple[int, str, str]:
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(
            paramiko.RejectPolicy() if self.strict_host_key else paramiko.AutoAddPolicy()
        )
        try:
            pkey = self._get_pkey()
            client.connect(
                self.host, port=self.port, username=self.user,
                pkey=pkey, password=self.password if not pkey else None,
                timeout=self.timeout, banner_timeout=self.timeout, auth_timeout=self.timeout
            )
            full_cmd = cmd
            if env:
                exports = " ".join(f'{k}={shlex.quote(v)}' for k, v in env.items())
                full_cmd = f"{exports} {full_cmd}"
            if cwd:
                full_cmd = f"cd {shlex.quote(cwd)} && {full_cmd}"
            stdin, stdout, stderr = client.exec_command(full_cmd, timeout=timeout)
            out = stdout.read().decode("utf-8", errors="ignore")
            err = stderr.read().decode("utf-8", errors="ignore")
            rc = stdout.channel.recv_exit_status()
            return rc, out, err
        except socket.gaierror as e:
            raise SSHError(f"Unable to resolve SSH host '{self.host}': {e}") from e
        except paramiko.ssh_exception.NoValidConnectionsError as e:
            raise SSHError(f"Unable to connect to {self.host}:{self.port}: {e}") from e
        except Exception as e:
            raise SSHError(str(e))
        finally:
            client.close()


# ─────────────────────────────────────────────
# Ендпойнти: загальні
# ─────────────────────────────────────────────
@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "version": app.version}


# ─────────────────────────────────────────────
# Proxmox: LXC
# ─────────────────────────────────────────────
@app.get("/version")
def pve_version() -> Dict[str, Any]:
    try:
        prox = get_proxmox()
        return prox.version.get()
    except Exception as e:
        raise _http_500(f"/version failed: {e}")


@app.get("/nodes")
def list_nodes() -> List[Dict[str, Any]]:
    try:
        prox = get_proxmox()
        return prox.nodes.get()
    except Exception as e:
        raise _http_500(f"/nodes failed: {e}")


@app.get("/lxc")
def list_lxc(node: Optional[str] = Query(None, description="Назва вузла (наприклад, 'pve'). Якщо не вказано — візьмемо перший.")) -> List[Dict[str, Any]]:
    try:
        prox = get_proxmox()
        node_name = _default_node(prox, node)
        return prox.nodes(node_name).lxc.get()
    except Exception as e:
        raise _http_500(f"/lxc failed: {e}")


@app.get("/lxc-list")
def lxc_list_via_ssh() -> List[Dict[str, Any]]:
    try:
        return _ssh_pct_list()
    except HTTPException as e:
        raise e
    except Exception as e:
        raise _http_500(f"/lxc-list failed: {e}")


@app.post("/lxc/start")
def start_lxc(req: StartStopReq) -> Dict[str, Any]:
    try:
        prox = get_proxmox()
        node_name = _default_node(prox, req.node)
        res = prox.nodes(node_name).lxc(req.vmid).status.start.post()
        return {"ok": True, "task": res}
    except Exception as e:
        raise _http_500(f"/lxc/start failed: {e}")


@app.post("/lxc/stop")
def stop_lxc(req: StartStopReq, force: bool = Query(False, description="True — форсована зупинка")) -> Dict[str, Any]:
    try:
        prox = get_proxmox()
        node_name = _default_node(prox, req.node)
        if force:
            res = prox.nodes(node_name).lxc(req.vmid).status.stop.post(force=1)
        else:
            res = prox.nodes(node_name).lxc(req.vmid).status.shutdown.post()
        return {"ok": True, "task": res}
    except Exception as e:
        raise _http_500(f"/lxc/stop failed: {e}")


@app.post("/lxc/create")
def create_lxc(req: CreateLXCReq) -> Dict[str, Any]:
    try:
        prox = get_proxmox()
        node_name = _default_node(prox, req.node)

        payload: Dict[str, Any] = {
            "vmid": req.vmid,
            "hostname": req.hostname,
            "cores": req.cores,
            "memory": req.memory,
            "ostemplate": req.ostemplate,
            "storage": req.storage,
            "rootfs": f"{req.storage}:{req.rootfs_gb}",
            "unprivileged": int(req.unprivileged),
            "start": int(req.start),
        }

        net0 = f"name=eth0,bridge={req.bridge}"
        if req.ip_cidr:
            net0 += f",ip={req.ip_cidr.with_prefixlen}"
            if req.gateway:
                net0 += f",gw={req.gateway.compressed}"
        payload["net0"] = net0

        if req.ssh_public_key:
            payload["ssh-public-keys"] = req.ssh_public_key
        if req.password:
            payload["password"] = req.password

        if req.features:
            payload["features"] = {k: bool(v) for k, v in req.features.items()}

        task = prox.nodes(node_name).lxc.post(**payload)
        return {"created": True, "task": task, "vmid": req.vmid, "node": node_name}
    except Exception as e:
        raise _http_500(f"/lxc/create failed: {e}")


@app.post("/lxc/exec")
def lxc_exec(spec: LXCExecSpec) -> Dict[str, Any]:
    host, user, key = _require_pve_ssh()

    command = spec.cmd if spec.cmd is not None else " && ".join(spec.commands or [])
    cmd = f"pct exec {spec.vmid} -- bash -lc {shlex.quote(command)}"
    try:
        res = subprocess.run(["ssh", "-i", key, f"{user}@{host}", cmd],
                             capture_output=True, text=True, timeout=3600)
        return {"rc": res.returncode, "stdout": res.stdout, "stderr": res.stderr}
    except Exception as e:
        raise _http_500(f"/lxc/exec failed: {e}")


# ─────────────────────────────────────────────
# Deploy у LXC (через pct exec по SSH)
# ─────────────────────────────────────────────
@app.post("/deploy")
def deploy(spec: DeploySpec) -> Dict[str, Any]:
    host, user, key = _require_pve_ssh()

    ctx = {"repo_url": spec.repo_url, "workdir": spec.workdir}
    safe_ctx = {k: shlex.quote(v) for k, v in ctx.items()}

    def render(c: str) -> str:
        tmpl_str = re.sub(r"\{\{(\w+)\}\}", r"${\1}", c)
        template = Template(tmpl_str)
        return template.safe_substitute(safe_ctx)

    steps: List[Dict[str, Any]] = []
    commands = [*spec.setup, *spec.commands]
    for c in commands:
        inner = render(c)
        vmid = shlex.quote(str(spec.target_vmid))
        pct_cmd = f"pct exec {vmid} -- bash -lc {shlex.quote(inner)}"
        try:
            res = subprocess.run(["ssh", "-i", key, f"{user}@{host}", pct_cmd],
                                 capture_output=True, text=True, timeout=3600)
            steps.append({"cmd": inner, "rc": res.returncode, "stdout": res.stdout, "stderr": res.stderr})
            if res.returncode != 0:
                return {"ok": False, "steps": steps}
        except Exception as e:
            steps.append({"cmd": inner, "rc": -1, "stdout": "", "stderr": str(e)})
            return {"ok": False, "steps": steps}
    return {"ok": True, "steps": steps}


# ─────────────────────────────────────────────
# Універсальний SSH: виконання команд на будь-якому сервері
# ─────────────────────────────────────────────
@app.post("/ssh/run")
def ssh_run(spec: SSHSpec) -> Dict[str, Any]:
    runner = SSHRunner(**_resolve_ssh_connection(spec))
    try:
        rc, out, err = runner.run(spec.cmd, env=spec.env, cwd=spec.cwd, timeout=1800)
        return {"rc": rc, "stdout": out, "stderr": err}
    except SSHError as e:
        raise HTTPException(status_code=400, detail=f"/ssh/run failed: {e}") from e
    except Exception as e:
        raise _http_500(f"/ssh/run failed: {e}")


# ─────────────────────────────────────────────
# Запуск програм на віддаленому сервері
# ─────────────────────────────────────────────
@app.post("/apps/launch")
def apps_launch(spec: AppLaunchSpec) -> Dict[str, Any]:
    runner = SSHRunner(**_resolve_ssh_connection(spec))
    env = dict(spec.env or {})
    if spec.display:
        env["DISPLAY"] = spec.display

    prog = shlex.quote(spec.program)
    args = " ".join(shlex.quote(a) for a in spec.args)
    base_cmd = f"{prog} {args}".strip()

    if spec.background:
        log_file = f"/tmp/{os.path.basename(spec.program)}.log"
        cmd = f"nohup {base_cmd} >{shlex.quote(log_file)} 2>&1 & echo $!"
    else:
        cmd = base_cmd

    try:
        rc, out, err = runner.run(cmd, env=env, cwd=spec.cwd, timeout=120)
        return {"rc": rc, "stdout": out, "stderr": err}
    except SSHError as e:
        raise HTTPException(status_code=400, detail=f"/apps/launch failed: {e}") from e
    except Exception as e:
        raise _http_500(f"/apps/launch failed: {e}")


# ─────────────────────────────────────────────
# Віддалений браузер (headless або GUI)
# ─────────────────────────────────────────────
@app.post("/browser/open")
def browser_open(spec: BrowserSpec) -> Dict[str, Any]:
    runner = SSHRunner(**_resolve_ssh_connection(spec))

    try:
        def build_headless_cmd(bin_name: str) -> str:
            flags = [
                "--no-first-run",
                "--no-default-browser-check",
                "--disable-gpu",
                "--disable-software-rasterizer",
                "--disable-dev-shm-usage",
                f"--window-size={spec.window_size}",
            ]
            if spec.user_data_dir:
                flags.append(f"--user-data-dir={shlex.quote(spec.user_data_dir)}")
            flags += spec.extra_args

            if spec.action == "open":
                return " ".join([shlex.quote(bin_name), "--headless=new", *flags, shlex.quote(spec.url)])
            if spec.action == "screenshot":
                outp = spec.output_path or "/tmp/screenshot.png"
                return " ".join([
                    shlex.quote(bin_name),
                    "--headless=new",
                    *flags,
                    f"--screenshot={shlex.quote(outp)}",
                    shlex.quote(spec.url),
                ])
            if spec.action == "pdf":
                outp = spec.output_path or "/tmp/page.pdf"
                return " ".join([
                    shlex.quote(bin_name),
                    "--headless=new",
                    *flags,
                    f"--print-to-pdf={shlex.quote(outp)}",
                    shlex.quote(spec.url),
                ])
            raise HTTPException(400, f"Unsupported action: {spec.action}")

        # headless
        if spec.headless:
            for candidate in spec.browser_cmds:
                check = f"command -v {shlex.quote(candidate)} >/dev/null 2>&1"
                rc, _, _ = runner.run(check, timeout=10)
                if rc == 0:
                    cmd = build_headless_cmd(candidate)
                    rc2, out2, err2 = runner.run(cmd, timeout=180)
                    return {"rc": rc2, "stdout": out2, "stderr": err2, "used": candidate}
            raise _http_500(f"No browser found from list: {spec.browser_cmds}")

        # GUI (DISPLAY має бути налаштований на віддаленій машині)
        env = {}
        if os.getenv("DEFAULT_GUI_DISPLAY"):
            env["DISPLAY"] = os.getenv("DEFAULT_GUI_DISPLAY")

        # xdg-open спроба
        rc, out, err = runner.run(
            f"xdg-open {shlex.quote(spec.url)} >/dev/null 2>&1 & echo $!",
            timeout=10,
            env=env,
        )
        if rc == 0:
            return {"rc": rc, "stdout": out, "stderr": err, "used": "xdg-open"}

        # fallback: firefox/chrome без headless
        for candidate in ["firefox"] + spec.browser_cmds:
            check = f"command -v {shlex.quote(candidate)} >/dev/null 2>&1"
            rc2, _, _ = runner.run(check, timeout=10, env=env)
            if rc2 == 0:
                cmd = f"{shlex.quote(candidate)} {shlex.quote(spec.url)}"
                rc3, out3, err3 = runner.run(cmd, timeout=30, env=env)
                return {"rc": rc3, "stdout": out3, "stderr": err3, "used": candidate}
        raise _http_500("No GUI browser found (tried xdg-open, firefox, chrome/chromium).")
    except SSHError as e:
        raise HTTPException(status_code=400, detail=f"/browser/open failed: {e}") from e


# ─────────────────────────────────────────────
# Uvicorn launcher (локальний запуск/дебаг)
# ─────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
