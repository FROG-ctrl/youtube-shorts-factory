"""Telegram notifications and the optional n8n webhook."""

from __future__ import annotations

import json
import logging

import requests

log = logging.getLogger(__name__)


def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
) -> bool:
    """Send a text message to a Telegram chat."""
    if not bot_token or not chat_id:
        log.warning("telegram skipped: BOT_TOKEN or CHAT_ID missing")
        return False
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        response = requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=30,
        )
    except requests.RequestException as exc:
        log.warning("telegram request failed: %s", exc)
        return False
    if response.status_code != 200:
        log.warning("telegram HTTP %s: %s", response.status_code, response.text[:200])
        return False
    log.info("telegram notification sent")
    return True


def notify_start(bot_token: str, chat_id: str, message: str) -> None:
    send_message(bot_token, chat_id, f"🚀 <b>{message}</b>")


def notify_success(
    bot_token: str,
    chat_id: str,
    title: str,
    lang: str,
    link: str,
    duration: float,
) -> None:
    text = (
        "✅ <b>فيديو جاهز</b>\n"
        f"العنوان: {title}\n"
        f"اللغة: {lang}\n"
        f"المدة: {duration:.0f} ثانية\n"
        f"🔗 <a href=\"{link}\">فتح الملف</a>"
    )
    send_message(bot_token, chat_id, text)


def notify_failure(bot_token: str, chat_id: str, error: str, context: str = "") -> None:
    text = (
        "❌ <b>فشل التشغيل</b>\n"
        f"{context}\n"
        f"الخطأ: {error[:800]}"
    )
    send_message(bot_token, chat_id, text)


def notify_webhook(webhook_url: str, payload: dict) -> bool:
    """Post the run result to the n8n webhook."""
    if not webhook_url:
        return False
    try:
        response = requests.post(webhook_url, json=json.loads(json.dumps(payload)), timeout=30)
    except (requests.RequestException, TypeError) as exc:
        log.warning("n8n webhook failed: %s", exc)
        return False
    ok = response.status_code < 400
    log.info("n8n webhook status %s", response.status_code)
    return ok
