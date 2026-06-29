from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from .audio_features import TrackFeatures, analyze_file, audio_files, write_id3_tags
from .rekordbox_sync import (
    close_rekordbox,
    doctor,
    list_playlists,
    push_tracks_to_playlist,
    rekordbox_running,
    write_m3u,
    write_rekordbox_xml,
)
from .soundcloud_common import (
    archive_for_output,
    features_from_dict,
    features_to_dict,
)
from .soundcloud_config import CONFIG_PATH, AppConfig, load_config, save_config, update_output_paths
from .soundcloud_downloader import (
    DownloadEntry,
    DownloadResult,
    SoundCloudDownloadError,
    collect_download_plan,
    download_entries,
    paths_for_entries,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except (SoundCloudDownloadError, ValueError, RuntimeError, OSError) as exc:
        _emit("error", message=str(exc))
        return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="soundcloud-dl-bridge")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("config").set_defaults(handler=_cmd_config)

    save_config_parser = subparsers.add_parser("save-config")
    save_config_parser.add_argument("--payload", default="-")
    save_config_parser.set_defaults(handler=_cmd_save_config)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("urls", nargs="+")
    plan_parser.add_argument("--limit", type=int)
    plan_parser.set_defaults(handler=_cmd_plan)

    download_parser = subparsers.add_parser("download")
    download_parser.add_argument("urls", nargs="+")
    download_parser.add_argument("--output-dir", required=True)
    download_parser.add_argument("--workers", type=int, required=True)
    download_parser.add_argument("--fragments", type=int, required=True)
    download_parser.add_argument("--quality", required=True)
    download_parser.add_argument("--limit", type=int)
    download_parser.add_argument("--ffmpeg")
    download_parser.set_defaults(handler=_cmd_download)

    analyze_parser = subparsers.add_parser("analyze")
    analyze_parser.add_argument("paths", nargs="*")
    analyze_parser.add_argument("--folder")
    analyze_parser.add_argument("--output-dir")
    analyze_parser.add_argument("--entries-json", default="[]")
    analyze_parser.set_defaults(handler=_cmd_analyze)

    write_tags_parser = subparsers.add_parser("write-tags")
    write_tags_parser.add_argument("--payload", default="-")
    write_tags_parser.set_defaults(handler=_cmd_write_tags)

    import_files_parser = subparsers.add_parser("write-import-files")
    import_files_parser.add_argument("--payload", default="-")
    import_files_parser.set_defaults(handler=_cmd_write_import_files)

    subparsers.add_parser("rekordbox-running").set_defaults(handler=_cmd_rekordbox_running)
    subparsers.add_parser("close-rekordbox").set_defaults(handler=_cmd_close_rekordbox)
    subparsers.add_parser("list-playlists").set_defaults(handler=_cmd_list_playlists)

    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("--payload", default="-")
    push_parser.set_defaults(handler=_cmd_push)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--output-dir")
    doctor_parser.set_defaults(handler=_cmd_doctor)

    return parser


def _cmd_config(_args: argparse.Namespace) -> int:
    config = load_config()
    _print_json({"ok": True, "config": _config_to_dict(config)})
    return 0


def _cmd_save_config(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    config = load_config()
    defaults = asdict(AppConfig())
    for key, value in payload.items():
        if key not in defaults:
            continue
        setattr(config, key, value)
    if "output_dir" in payload:
        update_output_paths(config, Path(str(payload["output_dir"])).expanduser())
    save_config(config)
    _print_json({"ok": True, "config": _config_to_dict(config)})
    return 0


def _cmd_plan(args: argparse.Namespace) -> int:
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")
    plan = collect_download_plan(args.urls)
    entries = plan.entries[: args.limit] if args.limit else plan.entries
    _print_json(
        {
            "ok": True,
            "title": plan.title,
            "count": len(entries),
            "entries": [_entry_to_dict(entry) for entry in entries],
        }
    )
    return 0


def _cmd_download(args: argparse.Namespace) -> int:
    if args.workers < 1:
        raise ValueError("--workers must be at least 1")
    if args.fragments < 1:
        raise ValueError("--fragments must be at least 1")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be at least 1")

    output_dir = Path(args.output_dir).expanduser()
    _emit("status", message="Expanding SoundCloud URL(s)...")
    plan = collect_download_plan(args.urls)
    entries = plan.entries[: args.limit] if args.limit else plan.entries
    archive = archive_for_output(output_dir, plan.title)
    _emit(
        "plan",
        title=plan.title,
        count=len(entries),
        entries=[_entry_to_dict(entry) for entry in entries],
        output_dir=str(output_dir),
        archive=str(archive),
    )
    if not entries:
        _emit("done", ok=False, message="No downloadable tracks found.")
        return 1

    results = download_entries(
        entries,
        output_dir=output_dir,
        workers=args.workers,
        quality=args.quality,
        fragments=args.fragments,
        ffmpeg=args.ffmpeg,
        archive=archive,
        status=lambda message: _emit("status", message=message),
    )
    failed_path = _write_failed_report(results, output_dir)
    downloaded_paths = _paths_from_results(results)
    paths = paths_for_entries(output_dir, entries)
    if downloaded_paths and not paths:
        paths = downloaded_paths
    ok_count = len([result for result in results if result.ok])
    failed = [result for result in results if not result.ok]
    _emit(
        "done",
        ok=bool(paths),
        title=plan.title,
        count=len(entries),
        downloaded_count=ok_count,
        failed_count=len(failed),
        failed_report=str(failed_path) if failed_path else "",
        output_dir=str(output_dir.expanduser().resolve()),
        archive=str(archive),
        entries=[_entry_to_dict(entry) for entry in entries],
        paths=[str(path) for path in paths],
        downloaded_paths=[str(path) for path in downloaded_paths],
        failures=[_result_error_to_dict(result) for result in failed],
    )
    return 0 if paths else 1


def _cmd_analyze(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else None
    entries = _entries_from_json(args.entries_json)
    url_by_id = {entry.id: entry.url for entry in entries if entry.id}

    paths = [Path(path).expanduser() for path in args.paths]
    if args.folder:
        paths = audio_files(Path(args.folder).expanduser())
    if not paths:
        raise ValueError("no audio files found")

    features: list[TrackFeatures] = []
    for index, path in enumerate(paths, start=1):
        _emit("status", message=f"[{index}/{len(paths)}] analyzing {path.name}")
        item = analyze_file(path, output_dir=output_dir or path.parent)
        source_url = url_by_id.get(item.source_id, item.source_url)
        if source_url:
            item = replace(item, source_url=source_url)
        features.append(item)
    _emit(
        "done",
        ok=True,
        count=len(features),
        features=[features_to_dict(item) for item in features],
    )
    return 0


def _cmd_write_tags(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    features = _features_payload(payload)
    for index, item in enumerate(features, start=1):
        _emit("status", message=f"[{index}/{len(features)}] tagging {Path(item.path).name}")
        write_id3_tags(Path(item.path), item)
    _emit("done", ok=True, count=len(features))
    return 0


def _cmd_write_import_files(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    features = _features_payload(payload)
    output_dir = Path(payload.get("output_dir") or ".").expanduser()
    source_name = str(payload.get("source_name") or "SoundCloud")
    safe_name = "".join(ch if ch.isalnum() or ch in {" ", "-", "_"} else "-" for ch in source_name).strip()
    m3u = write_m3u(features, output_dir / f"{safe_name or 'SoundCloud'}.m3u8")
    xml = write_rekordbox_xml(features, output_dir / "rekordbox-import.xml")
    _emit("done", ok=True, m3u=str(m3u), xml=str(xml))
    return 0


def _cmd_rekordbox_running(_args: argparse.Namespace) -> int:
    _print_json({"ok": True, "running": rekordbox_running()})
    return 0


def _cmd_close_rekordbox(_args: argparse.Namespace) -> int:
    _print_json({"ok": True, "closed": close_rekordbox()})
    return 0


def _cmd_list_playlists(_args: argparse.Namespace) -> int:
    playlists = list_playlists()
    _print_json(
        {
            "ok": True,
            "playlists": [
                {
                    "id": playlist.id,
                    "name": playlist.name,
                    "path": playlist.path,
                    "is_folder": playlist.is_folder,
                    "song_count": playlist.song_count,
                }
                for playlist in playlists
            ],
        }
    )
    return 0


def _cmd_push(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    features = _features_payload(payload)
    playlist_name = str(payload.get("playlist_name") or "").strip()
    if not playlist_name:
        raise ValueError("playlist_name is required")
    result = push_tracks_to_playlist(
        features,
        playlist_name=playlist_name,
        playlist_id=payload.get("playlist_id") or None,
        create_playlist=bool(payload.get("create_playlist")),
    )
    _emit(
        "done",
        ok=True,
        playlist_name=result.playlist_name,
        added_to_collection=result.added_to_collection,
        already_in_collection=result.already_in_collection,
        added_to_playlist=result.added_to_playlist,
        already_in_playlist=result.already_in_playlist,
        added_cues=result.added_cues,
        skipped_cues=result.skipped_cues,
        added_loops=result.added_loops,
        skipped_loops=result.skipped_loops,
        backup_dir=str(result.backup_dir),
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else config.output_path
    _print_json({"ok": True, "doctor": doctor(output_dir)})
    return 0


def _config_to_dict(config: AppConfig) -> dict[str, Any]:
    data = asdict(config)
    data["output_dir"] = str(config.output_path)
    data["archive_path"] = str(config.archive_file)
    data["likes_url"] = config.likes_url
    data["config_path"] = str(CONFIG_PATH)
    return data


def _entry_to_dict(entry: DownloadEntry) -> dict[str, str]:
    return {
        "url": entry.url,
        "id": entry.id or "",
        "title": entry.title or "",
        "label": entry.label,
    }


def _entries_from_json(text: str) -> list[DownloadEntry]:
    raw = json.loads(text or "[]")
    entries: list[DownloadEntry] = []
    for item in raw:
        entries.append(
            DownloadEntry(
                url=str(item.get("url") or ""),
                id=str(item.get("id") or "") or None,
                title=str(item.get("title") or "") or None,
            )
        )
    return entries


def _paths_from_results(results: list[DownloadResult]) -> list[Path]:
    paths: list[Path] = []
    for result in results:
        if result.ok:
            paths.extend(result.output_paths)
    return sorted(set(paths))


def _write_failed_report(results: list[DownloadResult], output_dir: Path) -> Path | None:
    failed = [result for result in results if not result.ok]
    if not failed:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "failed-downloads.txt"
    lines = [f"{result.entry.url}\t{result.error}" for result in failed]
    path.write_text("\n".join(lines) + "\n")
    return path


def _result_error_to_dict(result: DownloadResult) -> dict[str, str]:
    return {
        "url": result.entry.url,
        "label": result.entry.label,
        "error": result.error or "",
    }


def _features_payload(payload: dict[str, Any]) -> list[TrackFeatures]:
    raw = payload.get("features")
    if not isinstance(raw, list):
        raise ValueError("payload.features must be a list")
    return [features_from_dict(item) for item in raw]


def _load_payload(value: str) -> dict[str, Any]:
    text = sys.stdin.read() if value == "-" else value
    if not text.strip():
        return {}
    raw = json.loads(text)
    if not isinstance(raw, dict):
        raise ValueError("payload must be a JSON object")
    return raw


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, default=str), flush=True)


def _emit(event: str, **payload: Any) -> None:
    _print_json({"event": event, **payload})


if __name__ == "__main__":
    raise SystemExit(main())
