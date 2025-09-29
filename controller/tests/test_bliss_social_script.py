from pathlib import Path

import pytest

from controller.bliss_social_automation import ADBCommandError, ContentGeneratorError
from controller.bliss_social_script import (
    SOCIAL_NETWORKS,
    PostRequest,
    generate_content,
    install_app,
    launch_app,
    post_to_social,
    upload_file,
)


class DummyAutomation:
    def __init__(self):
        self.ensure_calls = []
        self.install_calls = []
        self.launch_calls = []
        self.push_calls = []
        self.publish_calls = []
        self.generate_calls = []

    def ensure_app_installed(self, app):
        self.ensure_calls.append(app)

    def install_app(self, path, reinstall=False):
        self.install_calls.append((Path(path), reinstall))
        return "installed"

    def launch_app(self, app, activity=None):
        self.launch_calls.append((app, activity))
        return "launched"

    def push_assets(self, files, remote_directory):
        self.push_calls.append((tuple(files), remote_directory))
        return {str(path): f"{remote_directory}/{Path(path).name}" for path in files}

    def publish_post(self, app_name, **kwargs):
        self.publish_calls.append((app_name, kwargs))
        return f"posted:{app_name}"

    def generate_post_text(self, prompt, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        return f"generated:{prompt}"


@pytest.fixture()
def automation():
    return DummyAutomation()


def test_install_app_without_apk_checks_existing(automation):
    message = install_app("facebook", device=automation)

    assert message == f"{SOCIAL_NETWORKS['facebook'].app.package} already installed"
    assert automation.ensure_calls == [SOCIAL_NETWORKS["facebook"].app]


def test_install_app_with_apk_invokes_underlying_controller(tmp_path, automation):
    apk = tmp_path / "app.apk"
    apk.write_bytes(b"binary")

    result = install_app("instagram", apk, device=automation, reinstall=True)

    assert result == "installed"
    assert automation.install_calls == [(apk, True)]


def test_launch_app_uses_network_configuration(automation):
    result = launch_app("twitter", device=automation, activity="CustomActivity")

    assert result == "launched"
    assert automation.launch_calls == [
        (SOCIAL_NETWORKS["twitter"].app, "CustomActivity")
    ]


def test_upload_file_pushes_to_default_directory(tmp_path, automation):
    files = [tmp_path / "photo.jpg", tmp_path / "video.mp4"]
    for item in files:
        item.write_bytes(b"data")

    uploads = upload_file("threads", files, device=automation)

    expected_remote = SOCIAL_NETWORKS["threads"].remote_directory
    assert automation.push_calls == [(tuple(files), expected_remote)]
    assert list(uploads) == [str(path) for path in files]


def test_post_to_social_converts_request_data(tmp_path, automation):
    media = tmp_path / "image.jpg"
    media.write_bytes(b"image")
    request = PostRequest(
        text="Hello",
        subject="Greetings",
        media=[media],
        extras={"foo": "bar"},
        share_activity="CustomShare",
        generation_prompt="Write a caption",
        system_prompt="You are helpful",
        launch_before_share=True,
        launch_activity="com.example/.Main",
    )

    result = post_to_social("linkedin", request, device=automation)

    assert result == "posted:linkedin"
    app_name, kwargs = automation.publish_calls[0]
    assert app_name == "linkedin"
    assert kwargs["media"] == [media]
    assert kwargs["extras"] == {"foo": "bar"}
    assert kwargs["share_activity"] == "CustomShare"
    assert kwargs["generation_prompt"] == "Write a caption"
    assert kwargs["system_prompt"] == "You are helpful"
    assert kwargs["launch_before_share"] is True
    assert kwargs["launch_activity"] == "com.example/.Main"


def test_generate_content_returns_text(automation):
    result = generate_content("Write something", device=automation, system_prompt="System")

    assert result == "generated:Write something"
    prompt, kwargs = automation.generate_calls[0]
    assert prompt == "Write something"
    assert kwargs["system_prompt"] == "System"


def test_install_app_wraps_adb_errors():
    class FailingAutomation(DummyAutomation):
        def install_app(self, path, reinstall=False):
            raise ADBCommandError(["adb", "install"], 1, "", "boom")

    with pytest.raises(RuntimeError) as exc:
        install_app("facebook", Path("/tmp/app.apk"), device=FailingAutomation())

    assert "Failed to install facebook" in str(exc.value)


def test_generate_content_wraps_llm_errors():
    class FailingAutomation(DummyAutomation):
        def generate_post_text(self, prompt, **kwargs):
            raise ContentGeneratorError("nope")

    with pytest.raises(RuntimeError) as exc:
        generate_content("Prompt", device=FailingAutomation())

    assert "Failed to generate content" in str(exc.value)

