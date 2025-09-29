import json
from pathlib import Path
from types import SimpleNamespace
from typing import List, Sequence

import pytest

from controller.bliss_social_automation import BlissSocialAutomation, _load_batch_plan


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
