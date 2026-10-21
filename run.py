#!/usr/bin/env python3
"""Command line entry point for the shorts factory.

Usage::

    python run.py                 # produce every topic in every language
    python run.py --lang ar       # only the Arabic versions
    python run.py --topic ai-tools
    python run.py --check         # only check which services are reachable
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from shorts_factory import media, telegram
from shorts_factory.config import load_settings
from shorts_factory.pipeline import produce_video

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(name)s | %(message)s",
)
log = logging.getLogger("run")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="YouTube shorts factory")
    parser.add_argument("--lang", choices=["ar", "en"], help="produce a single language")
    parser.add_argument("--topic", help="topic id from config/topics.json")
    parser.add_argument("--check", action="store_true", help="check service availability and exit")
    parser.add_argument("--output", default="", help="output directory")
    parser.add_argument("--no-drive", action="store_true", help="skip the Drive upload")
    parser.add_argument("--no-telegram", action="store_true", help="skip Telegram notifications")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    settings = load_settings()
    if args.output:
        settings.output_dir = Path(args.output)
    if args.no_drive:
        settings.enable_drive = False
    if args.no_telegram:
        settings.enable_telegram = False

    if args.check:
        print("\n=== service check ===")
        print("gemini key :", "set" if settings.gemini_api_key else "MISSING")
        print("pexels key :", "set" if settings.pexels_api_key else "MISSING")
        print("pixabay key:", "set" if settings.pixabay_api_key else "MISSING")
        print("drive      :", "ready" if settings.has_drive else "not configured")
        print("telegram   :", "ready" if settings.telegram_bot_token and settings.telegram_chat_id else "not configured")
        print("n8n hook   :", "set" if settings.n8n_webhook_url else "not configured")
        for name, status in media.provider_status(
            settings.pexels_api_key, settings.pixabay_api_key
        ).items():
            print(f"provider {name:<10}: {status}")

        print("\n=== what is still missing ===")
        missing = []
        if not settings.gemini_api_key:
            missing.append(
                "GEMINI_API_KEY  -> https://aistudio.google.com/apikey  (blocks everything)"
            )
        if not settings.pixabay_api_key and not settings.pexels_api_key:
            missing.append(
                "PIXABAY_API_KEY -> https://pixabay.com/api/docs/  (free, best footage quality)"
            )
        if not settings.has_drive:
            missing.append(
                "GOOGLE_SERVICE_ACCOUNT -> Google Cloud console, create a service account,\n"
                "                       enable the Drive API, download the JSON and share the\n"
                "                       target folder with the service account email"
            )
        if not (settings.telegram_bot_token and settings.telegram_chat_id):
            missing.append(
                "TELEGRAM_BOT_TOKEN + TELEGRAM_CHAT_ID -> create the bot with @BotFather"
            )
        if not settings.n8n_webhook_url:
            missing.append(
                "N8N_WEBHOOK_URL -> import n8n/shorts-factory-notifications.json, activate it\n"
                "                   and copy the production webhook url"
            )
        if not missing:
            print("nothing - every service is configured")
        for item in missing:
            print(" -", item)
        print("\nrun: ./scripts/setup_secrets.sh   (sets them all at once)")
        return 0

    if not settings.gemini_api_key:
        log.error("GEMINI_API_KEY is missing, cannot generate scripts")
        return 2

    topics = settings.topics
    if args.topic:
        topics = [t for t in topics if t.id == args.topic]
        if not topics:
            log.error("unknown topic id %r", args.topic)
            return 2

    languages = settings.languages
    if args.lang:
        languages = [lang for lang in languages if lang.code == args.lang]
        if not languages:
            log.error("unknown language %r", args.lang)
            return 2

    run_started = datetime.now(timezone.utc)
    log.info(
        "starting run: %d topic(s) x %d language(s)", len(topics), len(languages)
    )

    if settings.enable_telegram:
        telegram.notify_start(
            settings.telegram_bot_token,
            settings.telegram_chat_id,
            f"بدء تشغيل مصنع الفيديوهات ({len(topics) * len(languages)} فيديو)",
        )

    results = []
    for topic in topics:
        for language in languages:
            log.info("=== %s / %s ===", topic.id, language.code)
            result = produce_video(settings, topic, language)
            results.append(result)

            if settings.enable_telegram:
                if result.ok:
                    telegram.notify_success(
                        settings.telegram_bot_token,
                        settings.telegram_chat_id,
                        result.title,
                        result.lang,
                        result.drive_link or "https://github.com/FROG-ctrl/youtube-shorts-factory",
                        result.duration,
                    )
                else:
                    telegram.notify_failure(
                        settings.telegram_bot_token,
                        settings.telegram_chat_id,
                        result.error,
                        f"{topic.id} / {language.code}",
                    )

    if settings.enable_n8n and settings.n8n_webhook_url:
        telegram.notify_webhook(
            settings.n8n_webhook_url,
            {
                "event": "shorts_factory_run",
                "started_at": run_started.isoformat(),
                "finished_at": datetime.now(timezone.utc).isoformat(),
                "videos": [
                    {
                        "topic": r.topic_id,
                        "lang": r.lang,
                        "title": r.title,
                        "path": str(r.path),
                        "duration": r.duration,
                        "drive_link": r.drive_link,
                        "error": r.error,
                    }
                    for r in results
                ],
            },
        )

    failures = [r for r in results if not r.ok]
    log.info(
        "run finished: %d ok, %d failed", len(results) - len(failures), len(failures)
    )
    for result in results:
        marker = "OK " if result.ok else "ERR"
        log.info("%s %s/%s %s", marker, result.topic_id, result.lang, result.path if result.ok else result.error[:120])

    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
