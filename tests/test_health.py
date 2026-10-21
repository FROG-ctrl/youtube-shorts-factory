"""Offline tests for the credential health checks.

They prove that a broken key is reported as broken (instead of silently
falling back) without needing real credentials or a network connection.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from shorts_factory import health  # noqa: E402


def _response(status_code: int, payload=None, text: str = ""):
    response = mock.Mock(status_code=status_code, text=text)
    response.json.return_value = payload if payload is not None else {}
    return response


def test_gemini_accepts_a_valid_key() -> None:
    with mock.patch.object(
        health.requests, "request", return_value=_response(200, {"models": [{"name": "a"}, {"name": "b"}]})
    ):
        check = health.check_gemini("key")
    assert check.state == health.OK
    assert "2 models" in check.detail


def test_gemini_reports_a_rejected_key() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(403)):
        check = health.check_gemini("key")
    assert check.state == health.BAD
    assert check.blocking is True


def test_gemini_without_a_key_is_missing_not_broken() -> None:
    check = health.check_gemini("")
    assert check.state == health.MISSING
    assert check.blocking is False


def test_pexels_401_is_reported_as_a_bad_key() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(401)):
        check = health.check_pexels("key")
    assert check.state == health.BAD
    assert "rejected" in check.detail


def test_pexels_rate_limit_still_counts_as_usable() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(429)):
        check = health.check_pexels("key")
    assert check.state == health.OK


def test_pixabay_valid_key() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(200, {"hits": []})):
        check = health.check_pixabay("key")
    assert check.state == health.OK


def test_telegram_rejects_a_bad_bot_token() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(404)):
        check = health.check_telegram("token")
    assert check.state == health.BAD


def test_telegram_rejects_an_unreachable_chat() -> None:
    with mock.patch.object(
        health.requests, "request", side_effect=[_response(200), _response(400)]
    ):
        check = health.check_telegram("token", "chat")
    assert check.state == health.BAD
    assert "chat" in check.detail


def test_telegram_with_only_a_token_is_still_usable() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(200)):
        check = health.check_telegram("token")
    assert check.state == health.OK


def test_n8n_webhook_that_answers() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(200)):
        check = health.check_n8n("https://n8n/webhook/shorts-factory")
    assert check.state == health.OK


def test_n8n_webhook_not_found_is_bad() -> None:
    with mock.patch.object(health.requests, "request", return_value=_response(404)):
        check = health.check_n8n("https://n8n/webhook/shorts-factory")
    assert check.state == health.BAD


def test_drive_folder_that_is_not_shared_is_bad() -> None:
    class FakeFiles:
        def get(self, **kwargs):
            def execute():
                raise RuntimeError("HttpError 404: not found")

            return SimpleNamespace(execute=execute)

    service = SimpleNamespace(files=lambda: FakeFiles())
    with mock.patch("shorts_factory.drive._build_service", return_value=service):
        check = health.check_drive("{}", "folder-id")
    assert check.state == health.BAD
    assert "not shared" in check.detail


def test_drive_with_a_reachable_folder_is_ok() -> None:
    class FakeFiles:
        def get(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"id": "folder-id"})

        def list(self, **kwargs):
            return SimpleNamespace(execute=lambda: {"files": []})

    service = SimpleNamespace(files=lambda: FakeFiles())
    with mock.patch("shorts_factory.drive._build_service", return_value=service):
        check = health.check_drive("{}", "folder-id")
    assert check.state == health.OK


def test_drive_with_an_unusable_service_account_is_bad() -> None:
    with mock.patch("shorts_factory.drive._build_service", side_effect=ValueError("bad json")):
        check = health.check_drive("not json")
    assert check.state == health.BAD


def test_check_all_reports_every_service() -> None:
    settings = SimpleNamespace(
        gemini_api_key="k",
        pexels_api_key="",
        pixabay_api_key="",
        google_service_account="",
        google_drive_folder_id="",
        telegram_bot_token="",
        telegram_chat_id="",
        n8n_webhook_url="",
    )
    with mock.patch.object(health.requests, "request", return_value=_response(200, {"models": []})):
        checks = health.check_all(settings)
    names = [check.name for check in checks]
    assert names == ["gemini", "pexels", "pixabay", "drive", "telegram", "n8n"]
    assert checks[0].state == health.OK
    assert checks[1].state == health.MISSING


if __name__ == "__main__":
    failures = 0
    for name, function in sorted(globals().items()):
        if not name.startswith("test_") or not callable(function):
            continue
        try:
            function()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc}")
    print(f"\n{failures} failure(s)")
    sys.exit(1 if failures else 0)
