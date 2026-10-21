"""Video assembly.

Clips are concatenated into a single silent 9:16 video, the narration track is
muxed in and the subtitles are burned in as an overlay.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)


def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{result.stderr[-1500:]}")


def concat_clips(clips: list[Path], out_path: Path, work_dir: Path) -> Path:
    """Concatenate the normalised clips into one continuous video."""
    if not clips:
        raise RuntimeError("no clips to concatenate")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    listing = work_dir / "clips.txt"
    listing.parent.mkdir(parents=True, exist_ok=True)
    listing.write_text(
        "\n".join(f"file '{clip.resolve()}'" for clip in clips) + "\n", encoding="utf-8"
    )
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy",
            str(out_path),
        ]
    )
    return out_path


def add_narration(video: Path, audio: Path, out_path: Path) -> Path:
    """Attach the narration track, trimming the video to the audio length."""
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-i", str(audio),
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
            "-shortest",
            str(out_path),
        ]
    )
    return out_path


def burn_subtitles(video: Path, ass: Path, out_path: Path, font_dir: Path = Path("/usr/share/fonts")) -> Path:
    """Burn the styled subtitles into the picture.

    The result is written to a temporary file first and moved into place only
    once ffmpeg succeeded, so an interrupted run never leaves a broken file.
    """
    # Make sure a font that can render the script is available.
    fonts = []
    if font_dir.exists():
        for pattern in ("**/*Noto*Arabic*.ttf", "**/*Noto*Arabic*.otf", "**/*DejaVuSans*.ttf"):
            fonts.extend(font_dir.glob(pattern))
    if not fonts:
        log.warning("no matching font found in %s, subtitles may render with fallback", font_dir)
        filter_complex = f"subtitles='{ass}':charenc=UTF-8"
    else:
        # The style already names the family, fontsdir only helps libass find it.
        log.info("using subtitle font %s", fonts[0])
        filter_complex = f"subtitles='{ass}':fontsdir='{font_dir}'"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    temp_out = out_path.with_suffix(".part.mp4")
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-i", str(video),
            "-vf", filter_complex,
            "-c:v", "libx264", "-preset", "medium", "-crf", "21",
            "-pix_fmt", "yuv420p", "-c:a", "copy",
            str(temp_out),
        ]
    )
    temp_out.replace(out_path)
    return out_path


def build_final_video(
    clips: list[Path],
    narration: Path,
    ass: Path,
    out_path: Path,
    work_dir: Path,
) -> Path:
    """Full assembly chain: concat -> narration -> burned subtitles."""
    silent = work_dir / "silent.mp4"
    narrated = work_dir / "narrated.mp4"
    concat_clips(clips, silent, work_dir)
    add_narration(silent, narration, narrated)
    burn_subtitles(narrated, ass, out_path)
    log.info("final video written to %s", out_path)
    return out_path


def probe(path: Path) -> dict:
    """Return basic media information for the produced file."""
    if shutil.which("ffprobe") is None:
        return {}
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        capture_output=True,
        text=True,
    )
    import json

    try:
        return json.loads(result.stdout)
    except ValueError:
        return {}
