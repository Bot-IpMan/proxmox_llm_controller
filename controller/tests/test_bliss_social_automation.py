import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List, Sequence

import pytest

from controller.bliss_social_automation import (
    ADBClient,
    BlissSocialAutomation,
    ContentGenerator,
    ContentGeneratorError,
    PPADBClient,
    _load_batch_plan,
)


class FakeADB:
    def __init__(self):
        self.connect_address = None
        self.serial = None
        self.push_calls = []
        self.run_calls = []
        self.mkdir_calls = []
        self.launch_calls = []
        self.monkey_calls = []

    def push(self, source: Path, destination: str) -> str:
        self.push_calls.append((Path(source), destination))
        return "1 file pushed"

    def run(self, args, timeout=None, check=True, capture_output=True):
        self.run_calls.append((list(args), timeout))
        return SimpleNamespace(stdout="OK\n", returncode=0)

    def ensure_remote_directory(self, path: str) -> None:
        self.mkdir_calls.append(path)

    def ensure_device_ready(self):
        return {"serial": self.serial or "FAKE", "status": "device"}

    def launch_activity(self, component: str, *, extras: Sequence[str] = ()):  # type: ignore[override]
        self.launch_calls.append((component, list(extras)))
        return "Activity launched"

    def launch_via_monkey(self, package: str) -> str:
        self.monkey_calls.append(package)
        return "Monkey launched"


@pytest.fixture()
def automation():
    return BlissSocialAutomation(adb=FakeADB())


def test_publish_batch_executes_posts_and_collects_results(tmp_path, automation):
    media_file = tmp_path / "image.jpg"
    media_file.write_bytes(b"binary")

    plans = [
        {
            "app": "twitter",
            "text": "Hello",
            "media": [str(media_file)],
            "launch_before_share": True,
        },
        {"app": "facebook", "text": "World"},
    ]

    results = automation.publish_batch(plans)

    assert [entry[0].name for entry in automation.adb.push_calls] == ["image.jpg"]
    assert automation.adb.mkdir_calls == ["/sdcard/Download"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "ok"
    # Ensure adb run was invoked for both applications
    assert len(automation.adb.run_calls) == 2
    assert "com.twitter.android" in " ".join(automation.adb.run_calls[0][0])
    assert "com.facebook.katana" in " ".join(automation.adb.run_calls[1][0])
    assert automation.adb.launch_calls[0][0] == "com.twitter.android/com.twitter.app.main.MainActivity"


def test_publish_batch_collects_errors(tmp_path, automation):
    plans = [
        {"app": "unknown"},
        {"app": "facebook", "text": "Second"},
    ]

    results = automation.publish_batch(plans)

    assert results[0]["status"] == "error"
    assert "Unknown social app" in results[0]["error"]
    assert results[1]["status"] == "ok"
    assert len(automation.adb.run_calls) == 1


def test_publish_batch_stop_on_error(tmp_path, automation):
    plans = [
        {"app": "unknown"},
        {"app": "facebook"},
    ]

    with pytest.raises(KeyError):
        automation.publish_batch(plans, stop_on_error=True)


def test_push_assets_transfers_files_and_returns_remote_paths(tmp_path, automation):
    file_path = tmp_path / "caption.txt"
    file_path.write_text("hello world", encoding="utf-8")

    uploads = automation.push_assets([file_path], remote_directory="/sdcard/Target")

    assert automation.adb.push_calls == [(file_path, "/sdcard/Target/caption.txt")]
    assert automation.adb.mkdir_calls == ["/sdcard/Target"]
    assert uploads[str(file_path.resolve())] == "/sdcard/Target/caption.txt"


def _extract_am_extras(command: Sequence[str]) -> List[str]:
    """Return the extras passed to ``am start`` from an adb command."""

    try:
        start_index = command.index("-a")
    except ValueError:
        return list(command)
    return command[start_index:]


def test_instagram_share_does_not_include_text(tmp_path, automation):
    media = tmp_path / "photo.jpg"
    media.write_bytes(b"binary")

    automation.publish_post("instagram", text="ignored", media=[media])

    command, _timeout = automation.adb.run_calls[-1]
    extras = " ".join(_extract_am_extras(command))
    assert "android.intent.extra.TEXT" not in extras
    assert "android.intent.extra.STREAM" in extras


def test_other_networks_keep_text_extra(tmp_path, automation):
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"binary")

    automation.publish_post("twitter", text="caption", media=[media])

    command, _timeout = automation.adb.run_calls[-1]
    extras = " ".join(_extract_am_extras(command))
    assert "android.intent.extra.TEXT" in extras
    assert "--grant-read-uri-permission" in command


def test_share_command_includes_default_category(tmp_path, automation):
    media = tmp_path / "asset.jpg"
    media.write_bytes(b"binary")

    automation.publish_post("facebook", text="hello", media=[media])

    command, _timeout = automation.adb.run_calls[-1]
    assert "-c" in command
    assert "android.intent.category.DEFAULT" in command


def test_publish_post_launches_activity_when_requested(automation):
    automation.publish_post("facebook", text="hello", launch_before_share=True)

    assert automation.adb.launch_calls, "Expected launch_activity to be invoked"
    component, extras = automation.adb.launch_calls[-1]
    assert component == "com.facebook.katana/com.facebook.katana.IntentUriHandler"
    assert extras == []


def test_publish_post_custom_launch_activity(automation):
    automation.publish_post(
        "facebook",
        text="hello",
        launch_before_share=True,
        launch_activity="com.facebook.katana/.ComposerActivity",
    )

    component, _extras = automation.adb.launch_calls[-1]
    assert component == "com.facebook.katana/.ComposerActivity"


def test_load_batch_plan_accepts_list(tmp_path):
    plan = [{"app": "twitter"}]
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))

    loaded = _load_batch_plan(path)
    assert loaded == plan


def test_load_batch_plan_accepts_wrapped_object(tmp_path):
    plan = {"posts": [{"app": "facebook"}]}
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(plan))

    loaded = _load_batch_plan(path)
    assert loaded == plan["posts"]


def test_load_batch_plan_invalid_structure(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({"wrong": []}))

    with pytest.raises(ValueError):
        _load_batch_plan(path)


def test_content_generator_openai(monkeypatch):
    calls = {}

    class DummyChatCompletion:
        @staticmethod
        def create(**kwargs):
            calls.update(kwargs)
            return {"choices": [{"message": {"content": "Generated text  "}}]}

    dummy_module = SimpleNamespace(ChatCompletion=DummyChatCompletion, api_key=None)
    monkeypatch.setitem(sys.modules, "openai", dummy_module)
    monkeypatch.setenv("OPENAI_API_KEY", "secret")

    generator = ContentGenerator(provider="openai", model="test-model", temperature=0.5, max_tokens=42)
    text = generator.generate("Write a post", system_prompt="system message")

    assert text == "Generated text"
    assert calls["model"] == "test-model"
    assert calls["messages"][0]["role"] == "system"
    assert calls["messages"][1]["content"] == "Write a post"
    assert calls["temperature"] == 0.5
    assert calls["max_tokens"] == 42
    assert dummy_module.api_key == "secret"


def test_content_generator_huggingface(monkeypatch):
    captured = {}

    def fake_pipeline(task, model=None, **kwargs):
        captured["task"] = task
        captured["model"] = model
        captured["pipeline_kwargs"] = kwargs

        def runner(prompt, **params):
            captured["prompt"] = prompt
            captured["params"] = params
            return [{"generated_text": "Result"}]

        return runner

    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=fake_pipeline))

    generator = ContentGenerator(provider="huggingface", model="distilgpt2", temperature=0.8, max_tokens=64)
    text = generator.generate("Compose", system_prompt="Guidelines")

    assert text == "Result"
    assert captured["task"] == "text-generation"
    assert captured["model"] == "distilgpt2"
    assert captured["prompt"].startswith("Guidelines")
    assert captured["params"]["temperature"] == 0.8
    assert captured["params"]["max_new_tokens"] == 64


def test_content_generator_huggingface_auto_detects_gpu(monkeypatch):
    captured = {}

    def fake_pipeline(task, model=None, **kwargs):
        captured.update({"task": task, "model": model, "kwargs": kwargs})

        def runner(prompt, **_params):
            return [{"generated_text": "GPU"}]

        return runner

    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def device_count():
            return 1

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=FakeCuda()))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=fake_pipeline))
    monkeypatch.delenv("BLISS_HF_DEVICE", raising=False)

    generator = ContentGenerator(provider="huggingface", model="gpt2")
    assert captured["kwargs"].get("device") == 0
    assert generator.generate("Hi") == "GPU"


def test_content_generator_huggingface_respects_device_env(monkeypatch):
    captured = {}

    def fake_pipeline(task, model=None, **kwargs):
        captured["kwargs"] = kwargs

        def runner(prompt, **_params):
            return [{"generated_text": "Manual"}]

        return runner

    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def device_count():
            return 2

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=FakeCuda()))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=fake_pipeline))
    monkeypatch.setenv("BLISS_HF_DEVICE", "cuda:1")

    generator = ContentGenerator(provider="huggingface", model="gpt2")
    assert captured["kwargs"].get("device") == 1
    assert generator.generate("Hi") == "Manual"


def test_content_generator_huggingface_device_auto_map(monkeypatch):
    captured = {}

    def fake_pipeline(task, model=None, **kwargs):
        captured["kwargs"] = kwargs

        def runner(prompt, **_params):
            return [{"generated_text": "AutoMap"}]

        return runner

    class FakeCuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def device_count():
            return 1

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=FakeCuda()))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=fake_pipeline))
    monkeypatch.setenv("BLISS_HF_DEVICE", "auto")

    generator = ContentGenerator(provider="huggingface", model="gpt2")
    assert captured["kwargs"].get("device_map") == "auto"
    assert "device" not in captured["kwargs"]
    assert generator.generate("Hi") == "AutoMap"


def test_content_generator_huggingface_cuda_without_gpu(monkeypatch):
    def fake_pipeline(*_args, **_kwargs):  # pragma: no cover - should not be invoked
        raise AssertionError("pipeline should not be called when CUDA is unavailable")

    class FakeCuda:
        @staticmethod
        def is_available():
            return False

        @staticmethod
        def device_count():
            return 0

    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=FakeCuda()))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(pipeline=fake_pipeline))
    monkeypatch.setenv("BLISS_HF_DEVICE", "cuda")

    with pytest.raises(ContentGeneratorError):
        ContentGenerator(provider="huggingface", model="gpt2")


def test_content_generator_missing_key(monkeypatch):
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(ChatCompletion=SimpleNamespace(create=lambda **_: None), api_key=None))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(ContentGeneratorError):
        ContentGenerator(provider="openai")


def test_publish_post_generates_text(monkeypatch, automation):
    class DummyGenerator:
        def __init__(self):
            self.calls = []

        def generate(self, prompt, system_prompt=None):
            self.calls.append((prompt, system_prompt))
            return "Generated"

    dummy = DummyGenerator()

    automation.publish_post(
        "facebook",
        generation_prompt="Write something",
        system_prompt="Rules",
        generator=dummy,
    )

    assert dummy.calls == [("Write something", "Rules")]
    assert any("Generated" in arg for arg in automation.adb.run_calls[0][0])


def test_generate_post_text_uses_options(monkeypatch):
    created = {}

    class DummyGenerator:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def generate(self, prompt, system_prompt=None):
            return f"text:{prompt}:{system_prompt}"

    monkeypatch.setattr("controller.bliss_social_automation.ContentGenerator", DummyGenerator)

    automation = BlissSocialAutomation(adb=FakeADB())
    text = automation.generate_post_text(
        "Prompt",
        generator_options={"provider": "huggingface", "temperature": 0.3},
        system_prompt="System",
    )

    assert text == "text:Prompt:System"
    assert created == {"provider": "huggingface", "temperature": 0.3}


def test_generate_post_text_conflict(automation):
    generator = SimpleNamespace(generate=lambda *_args, **_kwargs: "text")

    with pytest.raises(ValueError):
        automation.generate_post_text(
            "Prompt",
            generator=generator,
            generator_options={"provider": "openai"},
        )


def test_adb_client_resolves_host_port_env(monkeypatch):
    monkeypatch.delenv("BLISS_ADB_ADDRESS", raising=False)
    monkeypatch.setenv("BLISS_ADB_HOST", "10.0.0.5")
    monkeypatch.setenv("BLISS_ADB_PORT", "5555")

    client = ADBClient(connect_address=None)

    assert client.connect_address == "10.0.0.5:5555"


def test_ppadb_client_mirrors_core_operations(monkeypatch, tmp_path):
    commands = []
    installs = []
    uninstalls = []
    pushes = []

    class DummyDevice:
        serial = "FAKE-SERIAL"

        @staticmethod
        def get_state():
            return "device"

        @staticmethod
        def shell(command, timeout=None):  # pragma: no cover - timeout unused
            commands.append((command, timeout))
            return "OK"

        @staticmethod
        def install(path, reinstall=False):
            installs.append((Path(path), reinstall))
            return "Success"

        @staticmethod
        def uninstall(package, keepdata=False):
            uninstalls.append((package, keepdata))
            return "Success"

        @staticmethod
        def push(source, destination):
            pushes.append((Path(source), destination))

    class DummyClient:
        def __init__(self, host, port):
            self.host = host
            self.port = port
            self._devices = [DummyDevice()]

        def devices(self):
            return list(self._devices)

        def device(self, serial):
            for device in self._devices:
                if device.serial == serial:
                    return device
            return None

        @staticmethod
        def remote_connect(host, port):
            return f"connected to {host}:{port}"

        @staticmethod
        def remote_disconnect(host, port):
            return f"disconnected {host}:{port}"

    fake_ppadb = SimpleNamespace(client=SimpleNamespace(Client=DummyClient))
    monkeypatch.setitem(sys.modules, "ppadb", fake_ppadb)
    monkeypatch.setitem(sys.modules, "ppadb.client", fake_ppadb.client)

    client = PPADBClient(serial="FAKE-SERIAL")

    assert client.list_devices() == [{"serial": "FAKE-SERIAL", "status": "device"}]

    client.wait_for_device()

    shell_result = client.run(["shell", "am", "start"])
    assert shell_result.stdout.strip() == "OK"
    assert commands[-1][0] == "am start"

    apk = tmp_path / "app.apk"
    apk.write_bytes(b"binary")
    install_result = client.run(["install", str(apk)])
    assert "Success" in install_result.stdout
    assert installs == [(apk, False)]

    uninstall_result = client.run(["uninstall", "com.example.app"])
    assert "Success" in uninstall_result.stdout
    assert uninstalls == [("com.example.app", False)]

    media = tmp_path / "photo.jpg"
    media.write_bytes(b"data")
    push_result = client.run(["push", str(media), "/sdcard/photo.jpg"])
    assert "photo.jpg" in push_result.stdout
    assert pushes == [(media, "/sdcard/photo.jpg")]
    client.ensure_remote_directory("/sdcard/Created")
    assert commands[-1][0] == "mkdir -p /sdcard/Created"

    connect_result = client.run(["connect", "192.168.1.2:5555"])
    assert "connected" in connect_result.stdout

    disconnect_result = client.run(["disconnect", "192.168.1.2:5555"])
    assert "disconnected" in disconnect_result.stdout

    devices_output = client.run(["devices", "-l"])
    assert "FAKE-SERIAL" in devices_output.stdout
