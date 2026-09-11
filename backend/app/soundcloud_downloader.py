"""Parallel SoundCloud MP3 downloader built on yt-dlp."""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import threading
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .klickaud_downloader import download_mp3 as download_klickaud_mp3

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
    artist: str | None = None

    @property
    def label(self) -> str:
        return self.title or self.url


@dataclass(frozen=True)
class DownloadResult:
    entry: DownloadEntry
    ok: bool
    output_paths: tuple[Path, ...] = ()
    error: str | None = None
    reason: str | None = None
    download_method: str = "yt-dlp"
    fallback_attempted: bool = False
    primary_error: str | None = None


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
        print(f"error: {msg}", file=sys.stderr)


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


def collect_download_plan(
    urls: Sequence[str], *, verbose: bool = False, status: StatusCallback | None = None,
) -> DownloadPlan:
    if not urls:
        return DownloadPlan([])

    ydl_cls = _youtube_dl_cls()
    opts = {
        "extract_flat": "in_playlist",
        "skip_download": True,
        "quiet": not verbose,
        "no_warnings": not verbose,
        "ignoreerrors": False,
        "ignore_no_formats_error": True,
        # Resolve needs titles and artists, not each track's stream manifests.
        "extractor_args": {"soundcloud": {"formats": ["none"]}},
        "socket_timeout": 10,
        "extractor_retries": 0,
        "retries": 0,
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

    missing = [(index, entry) for index, entry in enumerate(deduped) if not entry.title or entry.title == entry.id]
    if missing:
        def resolve_entry(entry: DownloadEntry) -> DownloadEntry:
            try:
                # YoutubeDL instances are not shared between worker threads.
                with ydl_cls(opts) as metadata_ydl:
                    metadata = metadata_ydl.extract_info(entry.url, download=False) or {}
                return replace(entry, title=_pick_str(metadata, "title") or entry.title,
                               artist=_metadata_artist(metadata) or entry.artist)
            except Exception:
                return entry

        if status:
            status(f"Reading track details: 0/{len(missing)}")
        with ThreadPoolExecutor(max_workers=min(4, len(missing))) as executor:
            futures = {executor.submit(resolve_entry, entry): index for index, entry in missing}
            for completed, future in enumerate(as_completed(futures), start=1):
                deduped[futures[future]] = future.result()
                if status:
                    status(f"Reading track details: {completed}/{len(missing)}")

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

    if archive:
        _retry_missing_archived_entries(archive, output_dir, entries)

    lock = threading.Lock()

    def emit(message: str) -> None:
        if status is None:
            return
        with lock:
            status(message)

    def run_one(index: int, entry: DownloadEntry) -> DownloadResult:
        emit(f"[{index}/{len(entries)}] downloading {entry.url}")
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
            primary_error = str(exc)
            emit(f"[{index}/{len(entries)}] trying KlickAud fallback for {entry.url}")
            try:
                fallback_path = download_klickaud_mp3(
                    entry.url,
                    _klickaud_destination(output_dir, entry),
                    status=lambda message: emit(
                        f"[{index}/{len(entries)}] KlickAud: {message} for {entry.url}"
                    ),
                )
                _write_download_metadata(fallback_path, entry)
                if archive and entry.id:
                    with lock:
                        _record_download_archive(archive, entry.id)
                result = DownloadResult(
                    entry=entry,
                    ok=True,
                    output_paths=(fallback_path,),
                    download_method="klickaud",
                    fallback_attempted=True,
                    primary_error=primary_error,
                )
                emit(f"[{index}/{len(entries)}] KlickAud fallback succeeded for {entry.url}")
            except Exception as fallback_exc:
                return DownloadResult(
                    entry=entry,
                    ok=False,
                    error=(
                        f"yt-dlp: {primary_error}; "
                        f"KlickAud fallback: {fallback_exc}"
                    ),
                    reason=download_failure_reason(primary_error),
                    download_method="none",
                    fallback_attempted=True,
                    primary_error=primary_error,
                )

        if result.download_method == "yt-dlp":
            emit(f"[{index}/{len(entries)}] primary download succeeded for {entry.url}")
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


def _retry_missing_archived_entries(
    archive: Path, output_dir: Path, entries: Sequence[DownloadEntry],
) -> None:
    archive = archive.expanduser()
    if not archive.is_file():
        return
    missing = {
        f"soundcloud {entry.id}" for entry in entries
        if entry.id and not _mp3_paths_for_entry(output_dir, entry)
    }
    original = archive.read_text()
    retained = [line for line in original.splitlines(keepends=True) if line.strip() not in missing]
    updated = "".join(retained)
    if updated == original:
        return
    temporary = archive.with_name(f".{archive.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(updated)
        os.replace(temporary, archive)
    finally:
        temporary.unlink(missing_ok=True)


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


def download_failure_reason(error: str) -> str:
    return "protected" if "drm protected" in error.lower() else "download"


def _write_download_metadata(path: Path, entry: DownloadEntry) -> None:
    from mutagen.easyid3 import EasyID3
    from mutagen.id3 import ID3NoHeaderError

    try:
        tags = EasyID3(path)
    except ID3NoHeaderError:
        tags = EasyID3()
    if entry.title and entry.title != entry.id:
        tags["title"] = entry.title
    if entry.artist:
        tags["artist"] = entry.artist
    tags.save(path)


def _klickaud_destination(output_dir: Path, entry: DownloadEntry) -> Path:
    title = entry.title or urlparse(entry.url).path.rstrip("/").rsplit("/", 1)[-1]
    safe_title = re.sub(r"[\x00-\x1f/:\\]+", "-", title).strip(" .-")[:180]
    safe_title = safe_title or "SoundCloud track"
    suffix = f" [{entry.id}]" if entry.id else ""
    return output_dir / f"{safe_title}{suffix}.mp3"


def _record_download_archive(archive: Path, track_id: str) -> None:
    archive = archive.expanduser()
    archive.parent.mkdir(parents=True, exist_ok=True)
    archive_line = f"soundcloud {track_id}"
    existing = set(archive.read_text().splitlines()) if archive.exists() else set()
    if archive_line not in existing:
        with archive.open("a", encoding="utf-8") as file:
            file.write(archive_line + "\n")


def import_local_replacement(
    source: Path,
    *,
    output_dir: Path,
    track_id: str,
    title: str,
    ffmpeg: str | None = None,
) -> Path:
    """Copy or convert a user-supplied licensed file into the managed folder."""
    source = source.expanduser().resolve()
    if not source.is_file():
        raise SoundCloudDownloadError(f"replacement audio file not found: {source}")
    if source.suffix.lower() not in {".mp3", ".wav", ".aiff", ".aif", ".flac", ".m4a", ".aac", ".ogg", ".opus"}:
        raise SoundCloudDownloadError(f"unsupported replacement audio type: {source.suffix or 'none'}")
    if not re.fullmatch(r"\d+", track_id):
        raise ValueError("a numeric SoundCloud track ID is required")

    output_dir = output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    safe_title = re.sub(r"[\x00-\x1f/:\\]+", "-", title).strip(" .-")[:180] or "SoundCloud track"
    destination = output_dir / f"{safe_title} [{track_id}].mp3"
    if source == destination:
        return destination

    temporary = destination.with_name(f".{destination.stem}.{os.getpid()}.tmp.mp3")
    try:
        if source.suffix.lower() == ".mp3":
            shutil.copy2(source, temporary)
        else:
            completed = subprocess.run(
                [
                    resolve_ffmpeg_location(ffmpeg),
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vn",
                    "-map_metadata",
                    "0",
                    "-codec:a",
                    "libmp3lame",
                    "-b:a",
                    "320k",
                    str(temporary),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip().splitlines()[-1:] or ["conversion failed"]
                raise SoundCloudDownloadError(detail[0])
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


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


def _metadata_artist(info: dict[str, Any]) -> str | None:
    artists = info.get("artists")
    if isinstance(artists, list) and artists:
        return ", ".join(str(artist) for artist in artists)
    return _pick_str(info, "artist") or _pick_str(info, "uploader")


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
            artist=_metadata_artist(info),
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
    return sorted(path for path in output_dir.glob("*.mp3") if path.is_file() and path.name.endswith(suffix))


def _youtube_dl_cls():
    from yt_dlp import YoutubeDL

    return YoutubeDL
