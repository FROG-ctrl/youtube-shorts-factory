"""End to end production pipeline for a single short."""

from __future__ import annotations

import logging
import shutil
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from . import compose, drive, media, subtitles, telegram, tts
from .config import Language, Settings, Topic
from .llm import Script, generate_script

log = logging.getLogger(__name__)


@dataclass
class VideoResult:
    topic_id: str
    lang: str
    title: str
    path: Path
    duration: float
    drive_link: str = ""
    script: dict = field(default_factory=dict)
    hashtags: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _safe_name(text: str, max_length: int = 60) -> str:
    keep = []
    for char in text:
        if char.isalnum() or char in {" ", "-", "_"}:
            keep.append(char)
        elif char in {"\u0600"}:  # keep Arabic letters
            keep.append(char)
    cleaned = " ".join("".join(keep).split())
    return (cleaned or "video")[:max_length].strip().replace(" ", "-")


def produce_video(
    settings: Settings,
    topic: Topic,
    language: Language,
    work_root: Path | None = None,
) -> VideoResult:
    """Produce one short for the given topic and language."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work_dir = (work_root or settings.output_dir) / f"{topic.id}_{language.code}_{stamp}"
    work_dir.mkdir(parents=True, exist_ok=True)
    result = VideoResult(topic_id=topic.id, lang=language.code, title="", path=work_dir / "video.mp4", duration=0.0)

    try:
        # 1. script -------------------------------------------------------
        script: Script = generate_script(
            api_key=settings.gemini_api_key,
            topic_title=topic.title(language.code),
            lang=language.code,
            seconds=settings.target_seconds,
            preferred_model=settings.gemini_model,
        )
        result.title = script.title
        result.script = script.to_dict()
        result.hashtags = script.hashtags
        log.info("script ready: %s", script.title)

        # 2. narration ----------------------------------------------------
        sentences = script.all_sentences
        segments = tts.synthesize(
            sentences=sentences,
            voice=language.tts_voice,
            rate=language.tts_rate,
            out_dir=work_dir / "audio",
        )
        if not segments:
            raise RuntimeError("text to speech produced no audio")
        narration = tts.concat_segments(segments, work_dir / "narration.m4a", work_dir)

        # 3. subtitles ----------------------------------------------------
        ass_path = subtitles.write_ass(segments, work_dir / "subtitles.ass", language.code)
        srt_path = subtitles.write_srt(segments, work_dir / "subtitles.srt", language.code)

        # 4. footage ------------------------------------------------------
        keywords = list(script.media_keywords) or list(topic.keywords_for(language.code))
        clips = media.search_clips(
            keywords=keywords,
            count=settings.max_clips,
            pexels_api_key=settings.pexels_api_key,
            pixabay_api_key=settings.pixabay_api_key,
        )
        audio_duration = sum(segment.duration for segment in segments)
        clip_count = max(len(clips), 1)
        per_clip = max(audio_duration / clip_count, 2.0)

        normalised: list[Path] = []
        clips_dir = work_dir / "clips"
        deadline = time.monotonic() + media.MEDIA_PHASE_BUDGET_SECONDS
        for index in range(clip_count):
            clip = clips[index] if index < len(clips) else media.Clip(provider="none", kind="video")
            normalised.append(
                media.materialize_clip(
                    clip,
                    dest_dir=clips_dir,
                    width=settings.video_width,
                    height=settings.video_height,
                    target_seconds=per_clip,
                    index=index,
                    deadline=deadline,
                )
            )
        media.cleanup_raw(clips_dir)

        # 5. render -------------------------------------------------------
        final_path = work_dir / f"{_safe_name(script.title)}.mp4"
        compose.build_final_video(
            clips=normalised,
            narration=narration,
            ass=ass_path,
            out_path=final_path,
            work_dir=work_dir,
        )
        result.path = final_path
        result.duration = audio_duration
        result.script["srt"] = str(srt_path)
        log.info("video ready: %s (%.1fs)", final_path, audio_duration)

        # 6. drive --------------------------------------------------------
        if settings.enable_drive and settings.has_drive:
            try:
                result.drive_link = drive.upload(
                    file_path=final_path,
                    account_json=settings.google_service_account,
                    folder_id=settings.google_drive_folder_id,
                    folder_name=f"{settings.channel_name} - {datetime.now(timezone.utc):%Y-%m-%d}",
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("drive upload failed: %s", exc)

        return result
    except Exception as exc:  # noqa: BLE001
        result.error = f"{exc}\n{traceback.format_exc()[-800:]}"
        log.error("production failed for %s/%s: %s", topic.id, language.code, exc)
        return result
    finally:
        # keep only the final artefacts, drop the intermediate files
        for path in (work_dir / "audio", work_dir / "clips"):
            shutil.rmtree(path, ignore_errors=True)
        for path in work_dir.glob("*.mp4"):
            if path != result.path:
                path.unlink(missing_ok=True)
