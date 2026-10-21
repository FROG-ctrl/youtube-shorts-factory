"""Script generation through the Gemini API.

The module keeps a small chain of candidate models: newer models are sometimes
unavailable for a given key, so we walk down the list until one answers.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field

import requests

log = logging.getLogger(__name__)

API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

#: Tried in order; the first model that answers is used for the whole run.
MODEL_FALLBACKS = [
    "gemini-3.8-flash",
    "gemini-3.7-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3.5-flash-lite",
    "gemini-3.1-flash-lite",
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
    "gemini-pro-latest",
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-2.0-flash",
]

#: HTTP statuses that mean "try again", not "this model will never work".
RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}
MAX_ATTEMPTS = 3
#: Busy models answer slowly, so a short timeout moves on to the next one fast.
REQUEST_TIMEOUT = 60

#: Remembers the model that answered last so the next video starts there.
_working_model: str = ""

ARABIC_PROMPT = """أنت كاتب محتوى محترف لقنوات يوتيوب شورتس.
اكتب سكريبت فيديو قصير مدته حوالي {seconds} ثانية عن: "{title}".

قواعد صارمة:
- ابدأ بجملة خطّافة (hook) قوية في أول 3 ثواني تجذب المشاهد.
- اكتب بين 5 و 8 جمل قصيرة، كل جملة جملة واحدة، سهلة القراءة بصوت عالي.
- استخدم لغة بسيطة وطبيعية، بدون مقدمات ولا مقدمات مطوّلة.
- لا تكتب أي إيموجي، ولا علامات تنسيق، ولا أرقام تعداد.
- لا تذكرللمشاهد "subscribe" أو "لايك".
- أرجع JSON فقط بدون أي نص إضافي.

الصيغة المطلوبة:
{{
  "title": "عنوان جذاب قصير لا يتجاوز 70 حرف",
  "hook": "الجملة الافتتاحية",
  "sentences": ["جملة 1", "جملة 2", "..."],
  "media_keywords": ["كلمة", "كلمة", "كلمة", "كلمة"],
  "hashtags": ["#وسم", "#وسم", "#وسم"]
}}"""

ENGLISH_PROMPT = """You are a professional script writer for YouTube Shorts.
Write a short video script of about {seconds} seconds about: "{title}".

Strict rules:
- Open with a strong hook in the first 3 seconds.
- Write between 5 and 8 short sentences, one idea per sentence, easy to read aloud.
- Use simple, natural spoken language. No preamble.
- No emojis, no markdown, no bullet numbers.
- Do not ask the viewer to subscribe or like.
- Return JSON only, with no extra text.

Required shape:
{{
  "title": "a catchy title under 70 characters",
  "hook": "the opening sentence",
  "sentences": ["sentence 1", "sentence 2", "..."],
  "media_keywords": ["keyword", "keyword", "keyword", "keyword"],
  "hashtags": ["#tag", "#tag", "#tag"]
}}"""


@dataclass
class Script:
    title: str
    hook: str
    sentences: list[str]
    media_keywords: list[str] = field(default_factory=list)
    hashtags: list[str] = field(default_factory=list)
    model: str = ""

    @property
    def all_sentences(self) -> list[str]:
        """Hook first, then the body sentences."""
        return [self.hook, *self.sentences] if self.hook else list(self.sentences)

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "hook": self.hook,
            "sentences": self.sentences,
            "media_keywords": self.media_keywords,
            "hashtags": self.hashtags,
            "model": self.model,
        }


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response that may contain fences."""
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"no JSON object found in response: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def _clean_sentence(sentence: str) -> str:
    sentence = sentence.strip().strip("\"'` ")
    sentence = re.sub(r"^[\-\*\d\.\)\s]+", "", sentence)
    return sentence.strip()


def list_available_models(api_key: str) -> list[str]:
    """Ask the API which models this key can actually use, best first.

    The catalogue changes often and some entries are only available to older
    projects, so the list is read at runtime instead of being hard coded.
    """
    try:
        response = requests.get(
            API_ROOT,
            headers={"x-goog-api-key": api_key},
            params={"pageSize": 200},
            timeout=30,
        )
        if response.status_code != 200:
            return []
        entries = response.json().get("models", [])
    except (requests.RequestException, ValueError) as exc:
        log.warning("could not list models: %s", exc)
        return []

    available = {
        item.get("name", "").replace("models/", "")
        for item in entries
        if "generateContent" in item.get("supportedGenerationMethods", [])
    }
    ordered = [m for m in MODEL_FALLBACKS if m in available]
    remaining = sorted(
        m
        for m in available
        if m not in ordered
        and "image" not in m
        and "tts" not in m
        and "omni" not in m
        and "robotics" not in m
    )
    return ordered + remaining


def _candidate_models(api_key: str, preferred_model: str) -> list[str]:
    """Build the ordered list of models to try for this run."""
    models: list[str] = []
    if preferred_model:
        models.append(preferred_model)
    if _working_model and _working_model not in models:
        models.append(_working_model)
    for model in list_available_models(api_key) + MODEL_FALLBACKS:
        if model not in models:
            models.append(model)
    return models


def generate_script(
    api_key: str,
    topic_title: str,
    lang: str,
    seconds: int = 45,
    preferred_model: str = "",
) -> Script:
    """Ask Gemini for a ready-to-record short script."""
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set")

    template = ARABIC_PROMPT if lang == "ar" else ENGLISH_PROMPT
    prompt = template.format(title=topic_title, seconds=seconds)

    models = _candidate_models(api_key, preferred_model)

    headers = {"Content-Type": "application/json", "x-goog-api-key": api_key}
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.9, "maxOutputTokens": 2048},
    }

    last_error: Exception | None = None
    for model in models:
        url = f"{API_ROOT}/{model}:generateContent"
        raw: str | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = requests.post(
                    url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT
                )
            except requests.RequestException as exc:  # pragma: no cover - network issue
                last_error = exc
                log.warning("model %s unreachable: %s", model, exc)
                break

            if response.status_code == 200:
                try:
                    raw = response.json()["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError, ValueError) as exc:
                    last_error = exc
                    log.warning("model %s returned an unexpected payload: %s", model, exc)
                    raw = None
                break

            last_error = RuntimeError(
                f"{model} -> HTTP {response.status_code}: {response.text[:200]}"
            )
            if response.status_code in RETRYABLE_STATUS and attempt < MAX_ATTEMPTS:
                wait = 4 * attempt
                log.warning(
                    "model %s busy (HTTP %s), retry %d/%d in %ss",
                    model, response.status_code, attempt, MAX_ATTEMPTS, wait,
                )
                time.sleep(wait)
                continue
            log.warning("model %s failed, trying next: %s", model, last_error)
            break

        if raw is None:
            continue

        try:
            payload_json = _extract_json(raw)
        except ValueError as exc:
            last_error = exc
            log.warning("model %s returned invalid JSON: %s", model, exc)
            continue

        sentences = [
            _clean_sentence(s) for s in payload_json.get("sentences", []) if s
        ]
        sentences = [s for s in sentences if s]
        if not sentences:
            last_error = ValueError("model returned no sentences")
            log.warning("model %s returned no sentences", model)
            continue

        log.info("script generated with model %s", model)
        global _working_model
        _working_model = model
        return Script(
            title=str(payload_json.get("title", topic_title)).strip(),
            hook=_clean_sentence(str(payload_json.get("hook", ""))),
            sentences=sentences,
            media_keywords=[
                str(k).strip() for k in payload_json.get("media_keywords", []) if k
            ],
            hashtags=[str(h).strip() for h in payload_json.get("hashtags", []) if h],
            model=model,
        )

    raise RuntimeError(f"all Gemini models failed: {last_error}")
