"""Text to speech using edge-tts (free, no API key required).

The narration is synthesised sentence by sentence so that the exact duration
of every line is known. Those durations drive the subtitle timing, which keeps
the burned-in captions perfectly in sync with the voice.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import edge_tts

log = logging.getLogger(__name__)


@dataclass
class Segment:
    """One synthesised sentence."""

    index: int
    text: str
    audio_path: Path
    duration: float


def _probe_duration(path: Path) -> float:
    """Return the duration of a media file in seconds using ffprobe."""
    if shutil.which("ffprobe") is None:
        return 0.0
    result = subprocess.run(
        [
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


async def _synthesize_one(text: str, voice: str, rate: str, out_path: Path) -> None:
    communicate = edge_tts.Communicate(text, voice, rate=rate)
    await communicate.save(str(out_path))


def synthesize(
    sentences: list[str],
    voice: str,
    rate: str,
    out_dir: Path,
    prefix: str = "seg",
) -> list[Segment]:
    """Synthesise every sentence and return the segments with their durations."""
    out_dir.mkdir(parents=True, exist_ok=True)
    segments: list[Segment] = []

    for index, sentence in enumerate(sentences):
        audio_path = out_dir / f"{prefix}_{index:02d}.mp3"
        if not audio_path.exists() or audio_path.stat().st_size < 512:
            try:
                asyncio.run(_synthesize_one(sentence, voice, rate, audio_path))
            except Exception as exc:  # noqa: BLE001
                log.warning("tts failed for %r: %s", sentence[:40], exc)
                continue
        duration = _probe_duration(audio_path)
        if duration <= 0:
            # rough fallback: Arabic ~ 14 chars/second, English ~ 15
            duration = max(len(sentence) / 14.0, 0.8)
        segments.append(
            Segment(index=index, text=sentence, audio_path=audio_path, duration=duration)
        )

    log.info("synthesised %d segments with %s", len(segments), voice)
    return segments


def concat_segments(segments: list[Segment], out_path: Path, work_dir: Path) -> Path:
    """Concatenate the per-sentence audio files into one narration track."""
    if not segments:
        raise RuntimeError("no audio segments to concatenate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listing = work_dir / "concat.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(
        "\n".join(f"file '{segment.audio_path.resolve()}'" for segment in segments) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ],
        check=True,
    )
    return out_path
