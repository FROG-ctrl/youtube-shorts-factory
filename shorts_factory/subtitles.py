"""Subtitle generation.

Two formats are produced:

* ``.srt`` - the plain subtitle file, also uploaded next to the video
* ``.ass`` - the styled version that ffmpeg burns into the picture
"""

from __future__ import annotations

import logging
from pathlib import Path

from .tts import Segment

log = logging.getLogger(__name__)

#: Arabic subtitles need a right aligned layout and a font with Arabic glyphs.
ARABIC_FONT = "Noto Sans Arabic"
LATIN_FONT = "DejaVu Sans"


def _timestamp(seconds: float, comma: bool = True) -> str:
    seconds = max(seconds, 0.0)
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    millis = int(round((seconds - int(seconds)) * 1000))
    if millis == 1000:
        millis = 0
        secs += 1
    separator = "," if comma else "."
    return f"{hours:02d}:{minutes:02d}:{secs:02d}{separator}{millis:03d}"


def write_srt(segments: list[Segment], out_path: Path, lang: str) -> Path:
    """Write a plain SRT file whose timings follow the real audio duration."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    cursor = 0.0
    for number, segment in enumerate(segments, start=1):
        start = cursor
        end = cursor + segment.duration
        cursor = end
        text = segment.text.replace("\n", " ").strip()
        lines.append(str(number))
        lines.append(f"{_timestamp(start)} --> {_timestamp(end)}")
        lines.append(text)
        lines.append("")
    out_path.write_text("\n".join(lines), encoding="utf-8")
    log.info("wrote srt %s", out_path.name)
    return out_path


def write_ass(segments: list[Segment], out_path: Path, lang: str) -> Path:
    """Write a styled ASS file ready to be burned into the video."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    is_rtl = lang == "ar"
    alignment = 2 if is_rtl else 2  # bottom-centre for both scripts
    font = ARABIC_FONT if is_rtl else LATIN_FONT

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1080
PlayResY: 1920
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},64,&H00FFFFFF,&H000000FF,&H00000000,&H96000000,1,0,0,0,100,100,0,0,1,4,2,{alignment},90,90,220,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    cursor = 0.0
    for segment in segments:
        start = cursor
        end = cursor + segment.duration
        cursor = end
        text = segment.text.replace("\n", " ").replace("{", "(").replace("}", ")").strip()
        if is_rtl:
            # The override block has to be closed before the text starts,
            # otherwise libass drops the whole line.
            text = "{\\rArabic}" + text
        events.append(
            f"Dialogue: 0,{_timestamp(start, comma=False)},{_timestamp(end, comma=False)},"
            f"Default,,0,0,0,,{text}"
        )

    out_path.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    log.info("wrote ass %s", out_path.name)
    return out_path
