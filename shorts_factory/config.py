"""Central configuration for the shorts factory.

Every secret is read from the environment so that nothing sensitive is ever
stored in the repository. GitHub Actions injects them from repository secrets.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
OUTPUT_DIR = Path(os.environ.get("OUTPUT_DIR", PROJECT_ROOT / "out"))


def _env(*names: str, default: str = "") -> str:
    """Return the first non-empty environment variable among ``names``."""
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return default


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Language:
    code: str
    name: str
    tts_voice: str
    tts_rate: str
    locale: str


@dataclass
class Topic:
    id: str
    title_ar: str
    title_en: str
    keywords: list[str] = field(default_factory=list)
    keywords_ar: list[str] = field(default_factory=list)

    def title(self, lang: str) -> str:
        return self.title_ar if lang == "ar" else self.title_en

    def keywords_for(self, lang: str) -> list[str]:
        return self.keywords_ar if lang == "ar" else self.keywords


@dataclass
class Settings:
    gemini_api_key: str
    pexels_api_key: str
    pixabay_api_key: str
    google_service_account: str
    google_drive_folder_id: str
    telegram_bot_token: str
    telegram_chat_id: str
    n8n_webhook_url: str
    gemini_model: str
    video_width: int
    video_height: int
    target_seconds: int
    max_clips: int
    enable_drive: bool
    enable_telegram: bool
    enable_n8n: bool
    output_dir: Path
    channel_name: str
    languages: list[Language] = field(default_factory=list)
    topics: list[Topic] = field(default_factory=list)

    @property
    def has_pexels(self) -> bool:
        return bool(self.pexels_api_key)

    @property
    def has_pixabay(self) -> bool:
        return bool(self.pixabay_api_key)

    @property
    def has_drive(self) -> bool:
        # The folder is created on demand, so the service account is enough.
        return bool(self.google_service_account)


def load_settings() -> Settings:
    """Build the :class:`Settings` object from the environment and config files."""
    plan: dict[str, Any] = {}
    plan_path = CONFIG_DIR / "topics.json"
    if plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))

    channel = plan.get("channel", {})
    languages = [Language(**lang) for lang in plan.get("languages", [])]
    topics = [Topic(**topic) for topic in plan.get("daily_topics", [])]

    return Settings(
        gemini_api_key=_env("GEMINI_API_KEY", "GOOGLE_API_KEY"),
        pexels_api_key=_env("PEXELS_API_KEY"),
        pixabay_api_key=_env("PIXABAY_API_KEY"),
        google_service_account=_env("GOOGLE_SERVICE_ACCOUNT", "GOOGLE_SERVICE_ACCOUNT_JSON"),
        google_drive_folder_id=_env("GOOGLE_DRIVE_FOLDER_ID"),
        telegram_bot_token=_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_env("TELEGRAM_CHAT_ID"),
        n8n_webhook_url=_env("N8N_WEBHOOK_URL"),
        gemini_model=_env("GEMINI_MODEL", ""),
        video_width=int(_env("VIDEO_WIDTH", default="1080")),
        video_height=int(_env("VIDEO_HEIGHT", default="1920")),
        target_seconds=int(_env("TARGET_SECONDS", default="45")),
        max_clips=int(_env("MAX_CLIPS", default="8")),
        enable_drive=_env_bool("ENABLE_DRIVE_UPLOAD", default=True),
        enable_telegram=_env_bool("ENABLE_TELEGRAM", default=True),
        enable_n8n=_env_bool("ENABLE_N8N", default=True),
        output_dir=OUTPUT_DIR,
        channel_name=channel.get("name", "Shorts Factory"),
        languages=languages,
        topics=topics,
    )
