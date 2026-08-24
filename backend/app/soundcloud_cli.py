from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .audio_features import analyze_file, audio_files, write_id3_tags
from .rekordbox_sync import doctor
from .soundcloud_common import archive_for_output, resolve_download_path
from .soundcloud_config import AppConfig, load_config
from .soundcloud_downloader import (
    DEFAULT_FRAGMENTS,
    DEFAULT_QUALITY,
    DEFAULT_WORKERS,
    DownloadResult,
    SoundCloudDownloadError,
    collect_download_plan,
    download_entries,
)

_resolve_download_path = resolve_download_path
_archive_for_output = archive_for_output


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="soundcloud-dl",
        description="SoundCloud downloader, analyzer, and Rekordbox importer backend.",
    )
    parser.add_argument("urls", nargs="*", help="SoundCloud URL(s)")
    parser.add_argument("-o", "--output-dir", type=Path)
    parser.add_argument("-w", "--workers", type=int, default=None)
    parser.add_argument("--fragments", type=int, default=None)
    parser.add_argument("--quality", default=None)
    parser.add_argument("--ffmpeg")
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("-i", "--interactive", action="store_true")
    parser.add_argument("-y", "--yes", action="store_true")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    config = load_config()

    try:
        if raw_args[:1] == ["sync-likes"]:
            args = _build_sync_parser().parse_args(raw_args[1:])
            likes_config = AppConfig(soundcloud_username=args.username or config.soundcloud_username)
            if not likes_config.likes_url:
                print(
                    "error: enter a SoundCloud username: soundcloud-dl sync-likes <username>",
                    file=sys.stderr,
                )
                return 2
            return _download_urls(
                [likes_config.likes_url],
                output_dir=config.output_path,
                workers=config.workers,
                fragments=config.fragments,
                quality=config.quality,
                ffmpeg=None,
                archive=None,
                dry_run=args.dry_run,
                limit=args.limit,
                verbose=False,
            )
        if raw_args[:1] == ["analyze"]:
            args = _build_analyze_parser().parse_args(raw_args[1:])
            return _analyze_command(args.folder or config.output_path, write_tags=args.write_tags)
        if raw_args[:1] == ["doctor"]:
            data = doctor(config.output_path)
            print(json.dumps(data, indent=2, default=str))
            return 0

        parser = build_parser()
        args = parser.parse_args(raw_args)
        if args.interactive or not args.urls:
            print("Interactive mode is provided by the soundcloud-dl launcher OpenTUI app.")
            print("Run the global soundcloud-dl binary with no arguments.")
            return 0
        return _download_main(args, parser, config)
    except KeyboardInterrupt:
        print("\nCancelled", file=sys.stderr)
        return 130


def _build_sync_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soundcloud-dl sync-likes")
    parser.add_argument("username", nargs="?", help="SoundCloud username")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--no-analyze", action="store_true")
    parser.add_argument("--no-tags", action="store_true")
    parser.add_argument("--no-rekordbox", action="store_true")
    return parser


def _build_analyze_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soundcloud-dl analyze")
    parser.add_argument("folder", nargs="?", type=Path)
    parser.add_argument("--write-tags", action="store_true")
    return parser


def _download_main(args: argparse.Namespace, parser: argparse.ArgumentParser, config: AppConfig) -> int:
    workers = args.workers or config.workers or DEFAULT_WORKERS
    fragments = args.fragments or config.fragments or DEFAULT_FRAGMENTS
    quality = args.quality or config.quality or DEFAULT_QUALITY
    output_dir = resolve_download_path(str(args.output_dir), config.output_path) if args.output_dir else config.output_path

    if workers < 1:
        parser.error("--workers must be at least 1")
    if fragments < 1:
        parser.error("--fragments must be at least 1")
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be at least 1")

    return _download_urls(
        args.urls,
        output_dir=output_dir,
        workers=workers,
        fragments=fragments,
        quality=quality,
        ffmpeg=args.ffmpeg,
        archive=args.archive,
        dry_run=args.dry_run,
        limit=args.limit,
        verbose=args.verbose,
    )


def _download_urls(
    urls: list[str],
    *,
    output_dir: Path,
    workers: int,
    fragments: int,
    quality: str,
    ffmpeg: str | None,
    archive: Path | None,
    dry_run: bool,
    limit: int | None,
    verbose: bool,
) -> int:
    try:
        plan = collect_download_plan(urls, verbose=verbose)
    except (SoundCloudDownloadError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    entries = plan.entries[:limit] if limit else plan.entries
    print(f"found {len(entries)} track(s)")
    if dry_run:
        for index, entry in enumerate(entries, start=1):
            print(f"{index:>3}. {entry.label}")
            if entry.title:
                print(f"     {entry.url}")
        return 0

    try:
        results = download_entries(
            entries,
            output_dir=output_dir,
            workers=workers,
            quality=quality,
            fragments=fragments,
            ffmpeg=ffmpeg,
            archive=archive or archive_for_output(output_dir, plan.title),
            verbose=verbose,
            status=print,
        )
    except (SoundCloudDownloadError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    failed = [result for result in results if not result.ok]
    if failed:
        _print_failures(failed)
        return 1

    print(f"downloaded {len(results)} track(s) to {output_dir.expanduser().resolve()}")
    return 0


def _analyze_command(folder: Path, *, write_tags: bool) -> int:
    files = audio_files(folder)
    if not files:
        print(f"No audio files found in {folder}")
        return 1

    print(f"Found {len(files)} audio file(s)")
    for index, path in enumerate(files, start=1):
        print(f"[{index}/{len(files)}] analyzing {path.name}")
        features = analyze_file(path, output_dir=folder)
        print(
            f"{features.title}\tBPM {features.bpm:.1f}\t"
            f"Key {features.camelot_key or features.musical_key}\tEnergy {features.energy}/10"
        )
        if write_tags:
            write_id3_tags(path, features)
    return 0


def _print_failures(failed: list[DownloadResult]) -> None:
    print(f"failed {len(failed)} track(s)", file=sys.stderr)
    for result in failed:
        print(f"- {result.entry.label}: {result.error}", file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
