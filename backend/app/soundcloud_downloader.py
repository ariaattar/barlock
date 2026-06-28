"""Parallel SoundCloud MP3 downloader built on yt-dlp."""
from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

DEFAULT_WORKERS = min(8, max(2, os.cpu_count() or 4))
DEFAULT_FRAGMENTS = 8
DEFAULT_QUALITY = "320"

StatusCallback = Callable[[str], None]


class SoundCloudDownloadError(RuntimeError):
    """Raised when a SoundCloud URL cannot be expanded or downloaded."""


@dataclass(frozen=True)
class DownloadEntry:
    url: str
    id: str | None = None
    title: str | None = None

    @property
    def label(self) -> str:
        return self.title or self.url


@dataclass(frozen=True)
class DownloadResult:
    entry: DownloadEntry
    ok: bool
    output_paths: tuple[Path, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class DownloadPlan:
    entries: list[DownloadEntry]
    title: str = ""


class _YtdlpLogger:
    def __init__(self, verbose: bool) -> None:
        self.verbose = verbose

    def debug(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def warning(self, msg: str) -> None:
        if self.verbose:
            print(f"warning: {msg}")

    def error(self, msg: str) -> None:
        print(f"error: {msg}")


def validate_soundcloud_url(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "soundcloud.com" or host.endswith(".soundcloud.com")
    ):
        raise ValueError(f"expected a SoundCloud URL, got: {url}")
    return url


def resolve_ffmpeg_location(explicit: str | None = None) -> str:
    if explicit:
        found = shutil.which(explicit)
        if found:
            return found
        path = Path(explicit).expanduser()
        if path.exists():
            return str(path)
        raise SoundCloudDownloadError(f"ffmpeg not found at: {explicit}")

    found = shutil.which("ffmpeg")
    if found:
        return found

    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise SoundCloudDownloadError(
            "ffmpeg is required for MP3 conversion. Install ffmpeg or install "
            "the backend dependencies so imageio-ffmpeg is available."
        ) from exc

    ffmpeg = Path(imageio_ffmpeg.get_ffmpeg_exe())
    if not ffmpeg.exists():
        raise SoundCloudDownloadError(f"imageio-ffmpeg returned a missing binary: {ffmpeg}")
    return str(ffmpeg)


def collect_download_plan(urls: Sequence[str], *, verbose: bool = False) -> DownloadPlan:
    if not urls:
        return DownloadPlan([])

    ydl_cls = _youtube_dl_cls()
    opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "ignoreerrors": False,
        "logger": _YtdlpLogger(verbose),
    }

    entries: list[DownloadEntry] = []
    collection_titles: list[str] = []
    with ydl_cls(opts) as ydl:
        for url in urls:
            validate_soundcloud_url(url)
            info = ydl.extract_info(url, download=False)
            title = _collection_title(info, url)
            if title:
                collection_titles.append(title)
            entries.extend(_flatten_info(info))

    deduped: list[DownloadEntry] = []
    seen: set[str] = set()
    for entry in entries:
        if entry.url.startswith(("http://", "https://")):
            validate_soundcloud_url(entry.url)
        key = entry.id or entry.url
        if key in seen:
            continue
        seen.add(key)
        deduped.append(entry)

    title = collection_titles[0] if len(set(collection_titles)) == 1 else ""
    return DownloadPlan(entries=deduped, title=title)


def collect_download_entries(urls: Sequence[str], *, verbose: bool = False) -> list[DownloadEntry]:
    return collect_download_plan(urls, verbose=verbose).entries


def download_entries(
    entries: Sequence[DownloadEntry],
    *,
    output_dir: Path,
    workers: int = DEFAULT_WORKERS,
    quality: str = DEFAULT_QUALITY,
    fragments: int = DEFAULT_FRAGMENTS,
    ffmpeg: str | None = None,
    archive: Path | None = None,
    verbose: bool = False,
    status: StatusCallback | None = None,
) -> list[DownloadResult]:
    if workers < 1:
        raise ValueError("workers must be at least 1")
    if fragments < 1:
        raise ValueError("fragments must be at least 1")
    if not entries:
        return []

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    ffmpeg_location = resolve_ffmpeg_location(ffmpeg)
    worker_count = min(workers, len(entries))

    lock = threading.Lock()

    def emit(message: str) -> None:
        if status is None:
            return
        with lock:
            status(message)

    def run_one(index: int, entry: DownloadEntry) -> DownloadResult:
        emit(f"[{index}/{len(entries)}] downloading {entry.label}")
        try:
            result = _download_one(
                entry,
                output_dir=output_dir,
                quality=quality,
                fragments=fragments,
                ffmpeg_location=ffmpeg_location,
                archive=archive,
                verbose=verbose,
            )
        except Exception as exc:  # yt-dlp raises several extractor-specific errors.
            return DownloadResult(entry=entry, ok=False, error=str(exc))

        if result.output_paths:
            files = ", ".join(path.name for path in result.output_paths)
            emit(f"[{index}/{len(entries)}] saved {files}")
        else:
            emit(f"[{index}/{len(entries)}] finished {entry.label}")
        return result

    results: list[DownloadResult | None] = [None] * len(entries)
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(run_one, index, entry): index - 1
            for index, entry in enumerate(entries, start=1)
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()

    return [result for result in results if result is not None]


def _download_one(
    entry: DownloadEntry,
    *,
    output_dir: Path,
    quality: str,
    fragments: int,
    ffmpeg_location: str,
    archive: Path | None,
    verbose: bool,
) -> DownloadResult:
    ydl_cls = _youtube_dl_cls()
    opts = _download_options(
        output_dir=output_dir,
        quality=quality,
        fragments=fragments,
        ffmpeg_location=ffmpeg_location,
        archive=archive,
        verbose=verbose,
    )
    with ydl_cls(opts) as ydl:
        ydl.extract_info(entry.url, download=True)
    return DownloadResult(
        entry=entry,
        ok=True,
        output_paths=tuple(_mp3_paths_for_entry(output_dir, entry)),
    )


def _download_options(
    *,
    output_dir: Path,
    quality: str,
    fragments: int,
    ffmpeg_location: str,
    archive: Path | None,
    verbose: bool,
) -> dict[str, Any]:
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": {
            "default": str(output_dir / "%(title).200B [%(id)s].%(ext)s"),
        },
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": quality,
            },
            {"key": "FFmpegMetadata", "add_metadata": True},
        ],
        "ffmpeg_location": ffmpeg_location,
        "concurrent_fragment_downloads": fragments,
        "continuedl": True,
        "ignoreerrors": False,
        "noplaylist": True,
        "no_warnings": not verbose,
        "quiet": not verbose,
        "retries": 10,
        "fragment_retries": 10,
        "windowsfilenames": True,
        "logger": _YtdlpLogger(verbose),
    }
    if archive:
        opts["download_archive"] = str(archive.expanduser())
    return opts


def _flatten_info(info: dict[str, Any] | None) -> list[DownloadEntry]:
    if not info:
        return []
    if "entries" in info:
        entries: list[DownloadEntry] = []
        for child in info.get("entries") or []:
            entries.extend(_flatten_info(child))
        return entries

    url = _pick_str(info, "webpage_url") or _pick_str(info, "original_url") or _pick_str(info, "url")
    if not url:
        return []
    return [
        DownloadEntry(
            url=url,
            id=_pick_str(info, "id"),
            title=_pick_str(info, "title"),
        )
    ]


def paths_for_entries(output_dir: Path, entries: Sequence[DownloadEntry]) -> list[Path]:
    paths: list[Path] = []
    root = output_dir.expanduser().resolve()
    for entry in entries:
        paths.extend(_mp3_paths_for_entry(root, entry))
    return sorted(set(paths))


def _collection_title(info: dict[str, Any] | None, url: str) -> str:
    if not info or "entries" not in info:
        return ""

    title = (
        _pick_str(info, "title")
        or _pick_str(info, "playlist_title")
        or _pick_str(info, "album")
        or ""
    ).strip()
    if title:
        return _clean_collection_title(title)

    parsed = urlparse(url)
    parts = [part for part in parsed.path.split("/") if part]
    if parts[-1:] == ["likes"] and parts[:1]:
        return f"{parts[0]} Likes"
    if "sets" in parts:
        index = parts.index("sets")
        if index + 1 < len(parts):
            return _slug_title(parts[index + 1])
    return _slug_title(parts[-1]) if parts else "SoundCloud"


def _clean_collection_title(title: str) -> str:
    title = title.strip()
    for suffix in (
        " playlist | Listen on SoundCloud",
        " playlist online for free on SoundCloud",
        " | Free Listening on SoundCloud",
    ):
        if title.endswith(suffix):
            title = title[: -len(suffix)].strip()
    return title


def _slug_title(value: str) -> str:
    return value.replace("-", " ").replace("_", " ").strip() or "SoundCloud"


def _pick_str(info: dict[str, Any], key: str) -> str | None:
    value = info.get(key)
    if value is None:
        return None
    return str(value)


def _mp3_paths_for_entry(output_dir: Path, entry: DownloadEntry) -> list[Path]:
    if not entry.id:
        return []
    suffix = f"[{entry.id}].mp3"
    return sorted(path for path in output_dir.glob("*.mp3") if path.name.endswith(suffix))


def _youtube_dl_cls():
    from yt_dlp import YoutubeDL

    return YoutubeDL
