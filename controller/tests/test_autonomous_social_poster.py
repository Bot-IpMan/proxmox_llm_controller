import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from controller.autonomous_social_poster import AutonomousSocialPoster, NETWORKS


class FakeAutomation:
    def __init__(self):
        self.ensure_app_installed_calls = []
        self.install_calls = []
        self.uninstall_calls = []
        self.launch_calls = []
        self.force_stop_calls = []
        self.push_assets_calls = []
        self.generate_calls = []
        self.publish_calls = []
        self.batch_calls = []
        self.adb = SimpleNamespace(list_devices=lambda: [{"serial": "FAKE", "status": "device"}])

    def ensure_app_installed(self, app, apk_path=None):
        self.ensure_app_installed_calls.append(app)

    def install_app(self, apk_path, reinstall=False):
        self.install_calls.append((apk_path, reinstall))
        return "installed"

    def uninstall_app(self, package, keep_data=False):
        self.uninstall_calls.append((package, keep_data))
        return "uninstalled"

    def launch_app(self, app, activity=None):
        self.launch_calls.append((app, activity))
        return "launched"

    def force_stop(self, app):
        self.force_stop_calls.append(app)

    def push_assets(self, files, remote_directory):
        self.push_assets_calls.append((tuple(files), remote_directory))
        return {str(path): f"{remote_directory}/{path.name}" for path in files}

    def generate_post_text(self, prompt, **kwargs):
        self.generate_calls.append((prompt, kwargs))
        return f"generated: {prompt}"

    def publish_post(self, app_name, **kwargs):
        self.publish_calls.append((app_name, kwargs))
        return f"posted to {app_name}"

    def publish_batch(self, plan, stop_on_error=False):
        self.batch_calls.append((plan, stop_on_error))
        return [{"status": "ok", "app": entry["app"]} for entry in plan]


@pytest.fixture()
def poster():
    return AutonomousSocialPoster(automation=FakeAutomation())


def test_install_app_with_apk_delegates_to_automation(tmp_path, poster):
    apk = tmp_path / "facebook.apk"
    apk.write_bytes(b"binary")

    result = poster.install_app("facebook", apk_path=apk, reinstall=True)

    assert result == "installed"
    assert poster.automation.install_calls == [(apk, True)]


def test_install_app_without_apk_checks_existing(poster):
    result = poster.install_app("twitter")

    assert result == f"{NETWORKS['twitter'].app.package} already installed"
    assert poster.automation.ensure_app_installed_calls == [NETWORKS["twitter"].app]


def test_uninstall_app_invokes_underlying_controller(poster):
    result = poster.uninstall_app("instagram", keep_data=True)

    assert result == "uninstalled"
    assert poster.automation.uninstall_calls == [
        (NETWORKS["instagram"].app.package, True)
    ]


def test_launch_app_uses_network_metadata(poster):
    result = poster.launch_app("reddit", activity="CustomActivity")

    assert result == "launched"
    assert poster.automation.launch_calls == [
        (NETWORKS["reddit"].app, "CustomActivity")
    ]


def test_force_stop_delegates_to_automation(poster):
    poster.force_stop("tiktok")

    assert poster.automation.force_stop_calls == [NETWORKS["tiktok"].app]


def test_push_content_uses_default_remote_directory(tmp_path, poster):
    media = [tmp_path / "image.jpg", tmp_path / "video.mp4"]
    for item in media:
        item.write_bytes(b"content")

    uploads = poster.push_content("threads", media)

    assert list(uploads) == [str(path) for path in media]
    assert poster.automation.push_assets_calls == [
        (tuple(media), NETWORKS["threads"].remote_directory)
    ]


def test_generate_content_invokes_llm(poster):
    text = poster.generate_content("linkedin", "Share updates")

    assert text == "generated: Share updates"
    assert poster.automation.generate_calls[0][0] == "Share updates"


def test_post_content_handles_media_and_generation(poster, tmp_path):
    photo = tmp_path / "photo.jpg"
    photo.write_bytes(b"binary")

    result = poster.post_content(
        "facebook",
        text="Hello",
        subject="Title",
        media=[photo],
        extras={"foo": "bar"},
        share_activity="CustomShare",
    )

    assert result == "posted to facebook"
    app_name, kwargs = poster.automation.publish_calls[0]
    assert app_name == "facebook"
    assert kwargs["media"] == [photo]
    assert kwargs["extras"] == {"foo": "bar"}
    assert kwargs["share_activity"] == "CustomShare"
    assert kwargs["launch_before_share"] is False
    assert kwargs["launch_activity"] is None


def test_post_content_requests_prelaunch(poster):
    result = poster.post_content(
        "instagram",
        text="Hello",
        launch_before_share=True,
        launch_activity="com.instagram.android/.MainTabActivity",
    )

    assert result == "posted to instagram"
    _, kwargs = poster.automation.publish_calls[-1]
    assert kwargs["launch_before_share"] is True
    assert kwargs["launch_activity"] == "com.instagram.android/.MainTabActivity"


def test_run_plan_validates_networks(poster):
    plan = [{"app": "twitter", "text": "hi"}]
    results = poster.run_plan(plan)

    assert results == [{"status": "ok", "app": "twitter"}]
    assert poster.automation.batch_calls == [(plan, False)]


def test_run_plan_rejects_missing_app_field(poster):
    with pytest.raises(KeyError):
        poster.run_plan([{"text": "oops"}])


def test_list_devices_returns_underlying_data(poster):
    devices = poster.list_devices()

    assert devices == [{"serial": "FAKE", "status": "device"}]


def test_invalid_network_name_raises_error(poster):
    with pytest.raises(KeyError):
        poster.get_network("unknown")
