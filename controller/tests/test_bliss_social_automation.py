import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import List, Sequence

import pytest

from controller.bliss_social_automation import (
    BlissSocialAutomation,
    ContentGenerator,
    ContentGeneratorError,
    _load_batch_plan,
)


class FakeADB:
    def __init__(self):
        self.connect_address = None
        self.serial = None
        self.push_calls = []
        self.run_calls = []

    def push(self, source: Path, destination: str) -> str:
        self.push_calls.append((Path(source), destination))
        return "1 file pushed"

    def run(self, args, timeout=None, check=True, capture_output=True):
        self.run_calls.append((list(args), timeout))
        return SimpleNamespace(stdout="OK\n", returncode=0)


@pytest.fixture()
def automation():
    return BlissSocialAutomation(adb=FakeADB())


def test_publish_batch_executes_posts_and_collects_results(tmp_path, automation):
    media_file = tmp_path / "image.jpg"
    media_file.write_bytes(b"binary")

    plans = [
        {"app": "twitter", "text": "Hello", "media": [str(media_file)]},
        {"app": "facebook", "text": "World"},
    ]

    results = automation.publish_batch(plans)

    assert [entry[0].name for entry in automation.adb.push_calls] == ["image.jpg"]
    assert results[0]["status"] == "ok"
    assert results[1]["status"] == "ok"
    # Ensure adb run was invoked for both applications
    assert len(automation.adb.run_calls) == 2
    assert "com.twitter.android" in " ".join(automation.adb.run_calls[0][0])
    assert "com.facebook.katana" in " ".join(automation.adb.run_calls[1][0])


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
