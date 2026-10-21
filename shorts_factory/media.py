"""Stock footage acquisition with a provider fallback chain.

Providers are tried in order of quality:

1. Pexels       - best quality, needs a valid API key
2. Pixabay      - very good quality, free API key
3. Internet Archive - free and keyless, quality varies
4. Wikimedia Commons - free and keyless, quality varies
5. Generated gradient - always available, so the pipeline never breaks
"""

from __future__ import annotations

import logging
import random
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import requests

log = logging.getLogger(__name__)

USER_AGENT = "youtube-shorts-factory/1.0 (+https://github.com/FROG-ctrl/youtube-shorts-factory)"

#: Never download more than this from a single source file.
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
#: The whole footage phase gets a time budget, after which gradients are used.
MEDIA_PHASE_BUDGET_SECONDS = 240
#: Source files bigger than this are skipped during the search.
MAX_SOURCE_BYTES = 120 * 1024 * 1024

GRADIENT_PALETTES = [
    ("0x0b1020", "0x2b5876"),
    ("0x1f1c2c", "0x928dab"),
    ("0x0f2027", "0x2c5364"),
    ("0x232526", "0x414345"),
    ("0x3a1c71", "0xd76d77"),
    ("0x16222a", "0x3a6073"),
]


@dataclass
class Clip:
    """A candidate piece of footage."""

    provider: str
    kind: str  # "video" or "photo"
    url: str = ""
    duration: float = 0.0
    width: int = 0
    height: int = 0
    label: str = ""

    @property
    def usable(self) -> bool:
        return bool(self.url)


# --------------------------------------------------------------------------- #
# Providers
# --------------------------------------------------------------------------- #
def _search_pexels(keyword: str, count: int, api_key: str) -> list[Clip]:
    url = "https://api.pexels.com/videos/search"
    response = requests.get(
        url,
        headers={"Authorization": api_key},
        params={"query": keyword, "per_page": max(count, 5), "orientation": "portrait"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"pexels HTTP {response.status_code}: {response.text[:120]}")
    clips: list[Clip] = []
    for item in response.json().get("videos", []):
        files = item.get("video_files", [])
        if not files:
            continue
        # prefer a small HD file: enough for a 1080x1920 short, quick to download
        files = sorted(files, key=lambda f: f.get("height", 0))
        chosen = next(
            (f for f in files if 720 <= f.get("height", 0) <= 1920),
            files[-1] if files else None,
        )
        if not chosen:
            continue
        clips.append(
            Clip(
                provider="pexels",
                kind="video",
                url=chosen.get("link", ""),
                duration=float(item.get("duration") or 0),
                width=int(chosen.get("width") or 0),
                height=int(chosen.get("height") or 0),
                label=keyword,
            )
        )
    return clips


def _search_pixabay(keyword: str, count: int, api_key: str) -> list[Clip]:
    url = "https://pixabay.com/api/videos/"
    response = requests.get(
        url,
        params={
            "key": api_key,
            "q": keyword,
            "per_page": max(count, 5),
            "video_type": "all",
            "safesearch": "true",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"pixabay HTTP {response.status_code}: {response.text[:120]}")
    clips: list[Clip] = []
    for item in response.json().get("hits", []):
        variants = item.get("videos", {}).get("large") or item.get("videos", {}).get("medium")
        if not variants:
            continue
        clips.append(
            Clip(
                provider="pixabay",
                kind="video",
                url=variants.get("url", ""),
                duration=float(item.get("duration") or 0),
                width=int(variants.get("width") or 0),
                height=int(variants.get("height") or 0),
                label=keyword,
            )
        )
    return clips


def _search_archive(keyword: str, count: int) -> list[Clip]:
    query = (
        "https://archive.org/advancedsearch.php"
        f"?q=mediatype%3Amovies+AND+({keyword.replace(' ', '+')})"
        "&fl%5B%5D=identifier&fl%5B%5D=title&rows=5&page=1&output=json"
    )
    response = requests.get(query, headers={"User-Agent": USER_AGENT}, timeout=30)
    if response.status_code != 200:
        raise RuntimeError(f"archive HTTP {response.status_code}")
    docs = response.json().get("response", {}).get("docs", [])
    clips: list[Clip] = []
    for doc in docs[:count]:
        identifier = doc.get("identifier")
        if not identifier:
            continue
        meta_url = f"https://archive.org/metadata/{identifier}"
        try:
            meta = requests.get(meta_url, headers={"User-Agent": USER_AGENT}, timeout=30).json()
        except requests.RequestException:
            continue
        for file_meta in meta.get("files", []):
            name = file_meta.get("name", "")
            size = int(file_meta.get("size") or 0)
            if not name.endswith((".mp4", ".webm")) or not size:
                continue
            if size > MAX_SOURCE_BYTES:
                log.info("skipping %s: %d MB is too large", name, size // (1024 * 1024))
                continue
            clips.append(
                Clip(
                    provider="archive",
                    kind="video",
                    url=f"https://archive.org/download/{identifier}/{name}",
                    duration=float(file_meta.get("length") or 0),
                    label=keyword,
                )
            )
            break
    return clips


def _search_wikimedia(keyword: str, count: int) -> list[Clip]:
    api = "https://commons.wikimedia.org/w/api.php"
    response = requests.get(
        api,
        params={
            "action": "query",
            "format": "json",
            "generator": "search",
            "gsrsearch": f"filetype:video {keyword}",
            "gsrlimit": count,
            "gsrnamespace": 6,
            "prop": "imageinfo",
            "iiprop": "url|size|mime|metadata",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"wikimedia HTTP {response.status_code}")
    pages = response.json().get("query", {}).get("pages", {})
    clips: list[Clip] = []
    for page in pages.values():
        infos = page.get("imageinfo", [])
        if not infos:
            continue
        info = infos[0]
        if not info.get("mime", "").startswith("video"):
            continue
        clips.append(
            Clip(
                provider="wikimedia",
                kind="video",
                url=info.get("url", ""),
                width=int(info.get("width") or 0),
                height=int(info.get("height") or 0),
                label=keyword,
            )
        )
    return clips


# --------------------------------------------------------------------------- #
# Chain
# --------------------------------------------------------------------------- #
def search_clips(
    keywords: list[str],
    count: int,
    pexels_api_key: str = "",
    pixabay_api_key: str = "",
) -> list[Clip]:
    """Return up to ``count`` clips, trying every provider that is configured."""
    providers: list[tuple[str, callable]] = []
    if pexels_api_key:
        providers.append(("pexels", lambda kw: _search_pexels(kw, count, pexels_api_key)))
    if pixabay_api_key:
        providers.append(("pixabay", lambda kw: _search_pixabay(kw, count, pixabay_api_key)))
    providers.append(("archive", lambda kw: _search_archive(kw, count)))
    providers.append(("wikimedia", lambda kw: _search_wikimedia(kw, count)))

    clips: list[Clip] = []
    seen_urls: set[str] = set()

    for keyword in keywords:
        if len(clips) >= count:
            break
        for provider_name, provider in providers:
            if len(clips) >= count:
                break
            try:
                found = provider(keyword)
            except Exception as exc:  # noqa: BLE001 - a dead provider must not kill the run
                log.warning("provider %s failed for %r: %s", provider_name, keyword, exc)
                continue
            for clip in found:
                if not clip.usable or clip.url in seen_urls:
                    continue
                seen_urls.add(clip.url)
                clips.append(clip)
            if found:
                break  # one provider per keyword is enough

    log.info(
        "collected %d clips from %s",
        len(clips),
        ", ".join(sorted({c.provider for c in clips})) or "no provider",
    )
    return clips


# --------------------------------------------------------------------------- #
# Materialisation
# --------------------------------------------------------------------------- #
def _run(cmd: list[str]) -> None:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd[:6])}...\n{result.stderr[-800:]}")


def _download(url: str, dest: Path, max_bytes: int = MAX_DOWNLOAD_BYTES) -> Path:
    """Download a file, refusing anything larger than ``max_bytes``."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(
        url, stream=True, headers={"User-Agent": USER_AGENT}, timeout=(20, 60)
    ) as response:
        response.raise_for_status()
        declared = int(response.headers.get("Content-Length") or 0)
        if declared and declared > max_bytes:
            raise RuntimeError(
                f"file is {declared // (1024 * 1024)} MB, above the {max_bytes // (1024 * 1024)} MB limit"
            )
        written = 0
        with dest.open("wb") as handle:
            for chunk in response.iter_content(chunk_size=1 << 16):
                if not chunk:
                    continue
                written += len(chunk)
                if written > max_bytes:
                    raise RuntimeError("download exceeded the size limit")
                handle.write(chunk)
    return dest


def _has_ffmpeg_filter(name: str) -> bool:
    try:
        result = subprocess.run(
            ["ffmpeg", "-hide_banner", "-filters"], capture_output=True, text=True, timeout=30
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.split()[1:2] == [name] for line in result.stdout.splitlines() if line.strip())


def generate_gradient(dest: Path, seconds: float, width: int, height: int, seed: int = 0) -> Path:
    """Render an animated gradient clip - the last-resort background."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    random.seed(seed)
    c0, c1 = random.choice(GRADIENT_PALETTES)
    duration = max(float(seconds), 1.0)
    if _has_ffmpeg_filter("gradients"):
        source = (
            f"gradients=s={width}x{height}:c0={c0}:c1={c1}:"
            f"speed=0.015:duration={duration:.2f}:seed={seed}"
        )
    else:  # pragma: no cover - very old ffmpeg
        source = f"color=c={c1}:s={width}x{height}:d={duration:.2f}"
    _run(
        [
            "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", source,
            "-vf", "format=yuv420p",
            "-t", f"{duration:.2f}",
            "-r", "30",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            str(dest),
        ]
    )
    return dest


def materialize_clip(
    clip: Clip,
    dest_dir: Path,
    width: int,
    height: int,
    target_seconds: float,
    index: int = 0,
    deadline: float | None = None,
) -> Path:
    """Download a clip and normalise it to a vertical 9:16 mp4 of a given length.

    Returns the path of the normalised file. Falls back to a generated
    gradient when the source cannot be fetched or decoded, or when the
    footage time budget is exhausted.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    final = dest_dir / f"clip_{index:02d}.mp4"
    if final.exists():
        return final

    out_of_time = deadline is not None and time.monotonic() > deadline
    if out_of_time:
        log.info("footage budget exhausted, using a generated gradient for clip %d", index)
        return generate_gradient(final, target_seconds, width, height, seed=index * 7 + 3)

    raw: Path | None = None
    if clip.usable:
        try:
            suffix = Path(clip.url.split("?")[0]).suffix or ".mp4"
            if suffix.lower() not in {".mp4", ".webm", ".mov", ".m4v", ".ogv"}:
                suffix = ".mp4"
            raw = _download(clip.url, dest_dir / f"raw_{index:02d}{suffix}")
        except Exception as exc:  # noqa: BLE001
            log.warning("download failed for %s: %s", clip.url, exc)
            raw = None

    if raw is not None:
        try:
            _run(
                [
                    "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                    "-i", str(raw),
                    "-vf",
                    (
                        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                        f"crop={width}:{height},setsar=1,format=yuv420p"
                    ),
                    "-t", f"{target_seconds:.2f}",
                    "-r", "30",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
                    "-an",
                    str(final),
                ]
            )
            if final.exists() and final.stat().st_size > 1024:
                return final
        except Exception as exc:  # noqa: BLE001
            log.warning("normalising %s failed: %s", raw, exc)

    log.info("falling back to a generated gradient for clip %d", index)
    return generate_gradient(final, target_seconds, width, height, seed=index * 7 + 3)


def cleanup_raw(dest_dir: Path) -> None:
    """Remove the downloaded source files once they have been normalised."""
    if not dest_dir.exists():
        return
    for path in dest_dir.glob("raw_*"):
        try:
            path.unlink()
        except OSError:
            shutil.rmtree(path, ignore_errors=True)


def provider_status(
    pexels_api_key: str = "",
    pixabay_api_key: str = "",
    include_keyed: bool = True,
) -> dict:
    """Quick health check of every configured provider, used by the CLI.

    ``include_keyed`` can be turned off when the keyed providers were already
    probed by :mod:`shorts_factory.health`, so they are not searched twice.
    """
    status: dict[str, str] = {}
    if pexels_api_key:
        try:
            clips = _search_pexels("nature", 1, pexels_api_key)
            status["pexels"] = f"ok ({len(clips)} clips)"
        except Exception as exc:  # noqa: BLE001
            status["pexels"] = f"error: {exc}"
    elif include_keyed:
        status["pexels"] = "no key"
    if pixabay_api_key:
        try:
            clips = _search_pixabay("nature", 1, pixabay_api_key)
            status["pixabay"] = f"ok ({len(clips)} clips)"
        except Exception as exc:  # noqa: BLE001
            status["pixabay"] = f"error: {exc}"
    elif include_keyed:
        status["pixabay"] = "no key"
    try:
        clips = _search_archive("nature", 1)
        status["archive"] = f"ok ({len(clips)} clips)"
    except Exception as exc:  # noqa: BLE001
        status["archive"] = f"error: {exc}"
    try:
        clips = _search_wikimedia("nature", 1)
        status["wikimedia"] = f"ok ({len(clips)} clips)"
    except Exception as exc:  # noqa: BLE001
        status["wikimedia"] = f"error: {exc}"
    return status
