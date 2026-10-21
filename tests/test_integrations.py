"""Offline tests for the Drive and Telegram integrations.

They prove the wiring is correct without needing real credentials: when the
user drops in a real service account and bot token the same code path runs.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_factory import drive, telegram  # noqa: E402

SERVICE_ACCOUNT = {
    "type": "service_account",
    "project_id": "demo",
    "private_key_id": "abc",
    "private_key": "-----BEGIN PRIVATE KEY-----\\nMIIB\\n-----END PRIVATE KEY-----\\n",
    "client_email": "factory@demo.iam.gserviceaccount.com",
    "client_id": "123",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class FakeFiles:
    def __init__(self, existing: list[dict] | None = None) -> None:
        self.existing = existing or []
        self.created: list[dict] = []

    def list(self, **kwargs):
        return SimpleNamespace(execute=lambda: {"files": self.existing})

    def create(self, **kwargs):
        self.created.append(kwargs)
        if "mimeType" in kwargs.get("body", {}):
            return SimpleNamespace(execute=lambda: {"id": "folder-id"})
        return SimpleNamespace(
            execute=lambda: {
                "id": "file-id",
                "name": kwargs["body"]["name"],
                "webViewLink": "https://drive.google.com/file/d/file-id",
            }
        )


class FakeService:
    def __init__(self, files: FakeFiles) -> None:
        self._files = files

    def files(self):
        return self._files


def test_drive_creates_the_folder_when_missing(tmp_path: Path) -> None:
    video = tmp_path / "short.mp4"
    video.write_bytes(b"0" * 2048)

    files = FakeFiles(existing=[])
    with mock.patch.object(drive, "_build_service", return_value=FakeService(files)):
        link = drive.upload(video, json.dumps(SERVICE_ACCOUNT), folder_id="", folder_name="demos")

    assert link == "https://drive.google.com/file/d/file-id"
    assert files.created[0]["body"]["mimeType"] == "application/vnd.google-apps.folder"
    assert files.created[0]["body"]["name"] == "demos"
    assert files.created[1]["body"]["parents"] == ["folder-id"]
    assert files.created[1]["body"]["name"] == "short.mp4"


def test_drive_reuses_an_existing_folder(tmp_path: Path) -> None:
    video = tmp_path / "short.mp4"
    video.write_bytes(b"0" * 2048)

    files = FakeFiles(existing=[{"id": "existing-id", "name": "demos"}])
    with mock.patch.object(drive, "_build_service", return_value=FakeService(files)):
        link = drive.upload(video, json.dumps(SERVICE_ACCOUNT), folder_id="", folder_name="demos")

    assert link.endswith("file-id")
    # The folder is reused, so the only create call is the file itself.
    assert len(files.created) == 1
    assert files.created[0]["body"]["parents"] == ["existing-id"]


def test_drive_uses_the_given_folder_id(tmp_path: Path) -> None:
    video = tmp_path / "short.mp4"
    video.write_bytes(b"0" * 2048)

    files = FakeFiles()
    with mock.patch.object(drive, "_build_service", return_value=FakeService(files)):
        drive.upload(video, json.dumps(SERVICE_ACCOUNT), folder_id="pinned-id")

    assert files.created[0]["body"]["parents"] == ["pinned-id"]


def test_service_account_key_newlines_are_fixed() -> None:
    info = json.loads(json.dumps(SERVICE_ACCOUNT))
    with mock.patch.object(drive.service_account.Credentials, "from_service_account_info") as factory:
        factory.return_value = mock.Mock()
        drive._build_service(json.dumps(SERVICE_ACCOUNT))

    passed = factory.call_args[0][0]
    assert "\n" in passed["private_key"]
    assert "\\n" not in passed["private_key"]


def test_telegram_posts_the_message() -> None:
    with mock.patch.object(telegram.requests, "post") as post:
        post.return_value = mock.Mock(status_code=200)
        assert telegram.send_message("token", "chat", "hello") is True

    url = post.call_args[0][0]
    assert url == "https://api.telegram.org/bottoken/sendMessage"
    assert post.call_args[1]["data"]["chat_id"] == "chat"
    assert post.call_args[1]["data"]["text"] == "hello"


def test_telegram_is_skipped_without_credentials() -> None:
    with mock.patch.object(telegram.requests, "post") as post:
        assert telegram.send_message("", "", "hello") is False
    post.assert_not_called()


def test_telegram_success_message_contains_the_link() -> None:
    with mock.patch.object(telegram.requests, "post") as post:
        post.return_value = mock.Mock(status_code=200)
        telegram.notify_success("token", "chat", "عنوان", "ar", "https://drive/x", 42.0)

    text = post.call_args[1]["data"]["text"]
    assert "https://drive/x" in text
    assert "عنوان" in text
    assert "42" in text


def test_n8n_webhook_payload() -> None:
    with mock.patch.object(telegram.requests, "post") as post:
        post.return_value = mock.Mock(status_code=200)
        assert telegram.notify_webhook(
            "https://n8n/webhook/shorts-factory", {"event": "x", "videos": []}
        ) is True

    assert post.call_args[0][0] == "https://n8n/webhook/shorts-factory"
    assert post.call_args[1]["json"] == {"event": "x", "videos": []}


if __name__ == "__main__":
    import inspect
    import tempfile

    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        wants_path = "tmp_path" in inspect.signature(function).parameters
        with tempfile.TemporaryDirectory() as folder:
            try:
                function(Path(folder)) if wants_path else function()
                print(f"PASS {name}")
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
