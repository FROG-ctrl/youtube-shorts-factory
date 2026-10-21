"""Live credential checks for every service the factory talks to.

``run.py --check`` uses this to tell the difference between a service that is
simply not configured and one that is configured with a broken key. It keeps
the failure mode of every provider in one place, so a bad secret is spotted
before a video is rendered instead of after.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import requests

log = logging.getLogger(__name__)

#: States a check can end in.
OK = "ok"
BAD = "bad"
MISSING = "missing"
ERROR = "error"

#: Services that block production entirely when they are broken.
BLOCKING = ("gemini",)

DEFAULT_TIMEOUT = 20


@dataclass
class ServiceCheck:
    """The result of probing a single service."""

    name: str
    state: str
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.state == OK

    @property
    def blocking(self) -> bool:
        return self.name in BLOCKING and self.state in {BAD, ERROR}

    def render(self) -> str:
        label = f"{self.name:<10}"
        if self.state == OK:
            return f"{label} ok      {self.detail}".rstrip()
        return f"{label} {self.state:<7} {self.detail}".rstrip()


def _request(method: str, url: str, timeout: int = DEFAULT_TIMEOUT, **kwargs: Any) -> requests.Response:
    return requests.request(method, url, timeout=timeout, **kwargs)


def check_gemini(api_key: str, timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the Gemini key is accepted and returns a model list."""
    if not api_key:
        return ServiceCheck("gemini", MISSING, "no key")
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    try:
        response = _request("GET", url, timeout=timeout, params={"key": api_key})
    except requests.RequestException as exc:
        return ServiceCheck("gemini", ERROR, f"network: {exc.__class__.__name__}")
    if response.status_code == 200:
        try:
            models = response.json().get("models", [])
        except ValueError:
            models = []
        return ServiceCheck("gemini", OK, f"{len(models)} models available")
    if response.status_code in (400, 401, 403):
        return ServiceCheck("gemini", BAD, f"HTTP {response.status_code}: key rejected")
    return ServiceCheck("gemini", ERROR, f"HTTP {response.status_code}")


def check_pexels(api_key: str, timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the Pexels key is accepted by the search endpoint."""
    if not api_key:
        return ServiceCheck("pexels", MISSING, "no key")
    url = "https://api.pexels.com/v1/search"
    try:
        response = _request(
            "GET",
            url,
            timeout=timeout,
            headers={"Authorization": api_key},
            params={"query": "technology", "per_page": 1},
        )
    except requests.RequestException as exc:
        return ServiceCheck("pexels", ERROR, f"network: {exc.__class__.__name__}")
    if response.status_code == 200:
        return ServiceCheck("pexels", OK, "key accepted")
    if response.status_code in (401, 403):
        return ServiceCheck("pexels", BAD, f"HTTP {response.status_code}: key rejected")
    if response.status_code == 429:
        return ServiceCheck("pexels", OK, "rate limited, key accepted")
    return ServiceCheck("pexels", ERROR, f"HTTP {response.status_code}")


def check_pixabay(api_key: str, timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the Pixabay key is accepted by the image/video API."""
    if not api_key:
        return ServiceCheck("pixabay", MISSING, "no key")
    url = "https://pixabay.com/api/"
    try:
        response = _request(
            "GET", url, timeout=timeout, params={"key": api_key, "q": "technology", "per_page": 1}
        )
    except requests.RequestException as exc:
        return ServiceCheck("pixabay", ERROR, f"network: {exc.__class__.__name__}")
    if response.status_code == 200:
        return ServiceCheck("pixabay", OK, "key accepted")
    if response.status_code in (400, 401, 403):
        return ServiceCheck("pixabay", BAD, f"HTTP {response.status_code}: key rejected")
    if response.status_code == 429:
        return ServiceCheck("pixabay", OK, "rate limited, key accepted")
    return ServiceCheck("pixabay", ERROR, f"HTTP {response.status_code}")


def check_drive(account_json: str, folder_id: str = "", timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the service account can reach Drive and the target folder."""
    if not account_json:
        return ServiceCheck("drive", MISSING, "no service account")
    try:
        from shorts_factory import drive as drive_module

        service = drive_module._build_service(account_json)
    except Exception as exc:  # noqa: BLE001 - any failure means the key is unusable
        return ServiceCheck("drive", BAD, f"{exc.__class__.__name__}: {exc}"[:120])
    try:
        if folder_id:
            service.files().get(
                fileId=folder_id, fields="id,name", supportsAllDrives=True
            ).execute()
            return ServiceCheck("drive", OK, f"folder {folder_id} reachable")
        service.files().list(pageSize=1, fields="files(id)").execute()
        return ServiceCheck("drive", OK, "authenticated")
    except Exception as exc:  # noqa: BLE001 - google api errors vary a lot
        text = str(exc)
        if "404" in text:
            return ServiceCheck("drive", BAD, "folder not shared with the service account")
        if "403" in text:
            return ServiceCheck("drive", BAD, "permission denied, share the folder")
        return ServiceCheck("drive", ERROR, f"{exc.__class__.__name__}: {exc}"[:120])


def check_telegram(bot_token: str, chat_id: str = "", timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the bot token works and that the chat id is reachable."""
    if not bot_token:
        return ServiceCheck("telegram", MISSING, "no bot token")
    try:
        response = _request("GET", f"https://api.telegram.org/bot{bot_token}/getMe", timeout=timeout)
    except requests.RequestException as exc:
        return ServiceCheck("telegram", ERROR, f"network: {exc.__class__.__name__}")
    if response.status_code != 200:
        return ServiceCheck("telegram", BAD, f"HTTP {response.status_code}: bot token rejected")
    if not chat_id:
        return ServiceCheck("telegram", OK, "bot accepted, no chat id")
    try:
        chat = _request(
            "GET", f"https://api.telegram.org/bot{bot_token}/getChat", timeout=timeout,
            params={"chat_id": chat_id},
        )
    except requests.RequestException as exc:
        return ServiceCheck("telegram", ERROR, f"network: {exc.__class__.__name__}")
    if chat.status_code == 200:
        return ServiceCheck("telegram", OK, f"bot ok, chat {chat_id} reachable")
    return ServiceCheck("telegram", BAD, f"HTTP {chat.status_code}: chat {chat_id} unreachable")


def check_n8n(webhook_url: str, timeout: int = DEFAULT_TIMEOUT) -> ServiceCheck:
    """Check that the n8n webhook endpoint answers."""
    if not webhook_url:
        return ServiceCheck("n8n", MISSING, "no webhook url")
    try:
        response = _request(
            "POST",
            webhook_url,
            timeout=timeout,
            json={"event": "shorts_factory_check", "videos": []},
        )
    except requests.RequestException as exc:
        return ServiceCheck("n8n", ERROR, f"network: {exc.__class__.__name__}")
    if response.status_code < 400:
        return ServiceCheck("n8n", OK, f"HTTP {response.status_code}")
    if response.status_code in (401, 403):
        return ServiceCheck("n8n", BAD, f"HTTP {response.status_code}: webhook needs auth")
    return ServiceCheck("n8n", BAD, f"HTTP {response.status_code}")


def check_all(settings: Any, timeout: int = DEFAULT_TIMEOUT) -> list[ServiceCheck]:
    """Probe every service configured in ``settings`` and return the results."""
    checks = [
        check_gemini(settings.gemini_api_key, timeout),
        check_pexels(settings.pexels_api_key, timeout),
        check_pixabay(settings.pixabay_api_key, timeout),
        check_drive(settings.google_service_account, settings.google_drive_folder_id, timeout),
        check_telegram(settings.telegram_bot_token, settings.telegram_chat_id, timeout),
        check_n8n(settings.n8n_webhook_url, timeout),
    ]
    for check in checks:
        log.info("%s -> %s %s", check.name, check.state, check.detail)
    return checks
