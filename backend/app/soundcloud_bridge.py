from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from .audio_features import TrackFeatures, analyze_file, analyze_one_worker, audio_files, write_id3_tags
from .rekordbox_sync import (
    close_rekordbox,
    doctor,
    list_playlist_tracks,
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
from .sync_state import (
    SyncState,
    derive_target_dir,
    diff_track_ids,
    load_state,
    safe_slug,
    save_state,
)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.handler(args) or 0)
    except (SoundCloudDownloadError, ValueError, RuntimeError, OSError) as exc:
        _emit("error", message=str(exc))
        return 1
    except Exception as exc:
        _emit("error", message=f"{type(exc).__name__}: {exc}")
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

    calibrate_parser = subparsers.add_parser("calibrate")
    calibrate_parser.add_argument("--manifest", required=True)
    calibrate_parser.set_defaults(handler=_cmd_calibrate)

    sync_plan_parser = subparsers.add_parser("sync-plan")
    sync_plan_parser.add_argument("url")
    sync_plan_parser.set_defaults(handler=_cmd_sync_plan)

    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--payload", default="-")
    sync_parser.set_defaults(handler=_cmd_sync)

    list_tracks_parser = subparsers.add_parser("list-playlist-tracks")
    list_tracks_parser.add_argument("--playlist-id", required=True)
    list_tracks_parser.set_defaults(handler=_cmd_list_playlist_tracks)

    reanalyze_parser = subparsers.add_parser("reanalyze-playlist")
    reanalyze_parser.add_argument("--payload", default="-")
    reanalyze_parser.set_defaults(handler=_cmd_reanalyze_playlist)

    return parser


def _default_analyze_workers() -> int:
    return max(1, min(os.cpu_count() or 4, 4))


def _analyze_paths_parallel(
    paths: list[Path],
    *,
    output_dir: Path | None,
    extract_vocal_stems: bool,
    use_cache: bool = True,
    workers: int | None = None,
) -> list[TrackFeatures]:
    """Analyze N audio paths concurrently, preserving input order.

    Workers run in a ProcessPoolExecutor — each worker imports librosa once
    on first task. For small batches we fall back to in-process serial work
    so we don't pay the worker startup cost.
    """
    if not paths:
        return []

    worker_count = workers if workers is not None else _default_analyze_workers()
    worker_count = max(1, min(worker_count, len(paths)))

    # For tiny batches the spawn cost dwarfs the parallel savings.
    if worker_count == 1 or len(paths) < 3:
        results: list[TrackFeatures] = []
        for index, path in enumerate(paths, start=1):
            _emit(
                "status",
                message=f"[{index}/{len(paths)}] analyzing {path.name}",
            )
            results.append(
                analyze_file(
                    path,
                    output_dir=output_dir or path.parent,
                    use_cache=use_cache,
                    extract_vocal_stems=extract_vocal_stems,
                )
            )
        return results

    total = len(paths)
    output_dir_str = str(output_dir) if output_dir is not None else ""
    work_items = [
        (str(path), output_dir_str, use_cache, extract_vocal_stems)
        for path in paths
    ]
    results_by_index: dict[int, TrackFeatures] = {}
    started: set[int] = set()
    finished = 0
    lock = threading.Lock()

    def emit_progress(track_name: str = "") -> None:
        with lock:
            in_progress = len(started) - finished
            tail = f" ({track_name})" if track_name else ""
            _emit(
                "status",
                message=f"[{finished}/{total} done, {max(in_progress, 0)} in progress] analyzing{tail}",
            )

    with ProcessPoolExecutor(max_workers=worker_count) as executor:
        futures = {}
        for index, item in enumerate(work_items):
            future = executor.submit(analyze_one_worker, item)
            futures[future] = (index, Path(item[0]).name)
            started.add(index)
        emit_progress()
        for future in as_completed(futures):
            index, name = futures[future]
            try:
                features = future.result()
            except Exception as exc:
                raise RuntimeError(f"analysis failed for {name}: {exc}") from exc
            results_by_index[index] = features
            with lock:
                finished += 1
            emit_progress(name)

    return [results_by_index[i] for i in range(total)]


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

    config = load_config()
    raw_features = _analyze_paths_parallel(
        paths,
        output_dir=output_dir,
        extract_vocal_stems=config.extract_vocal_stems,
    )
    features: list[TrackFeatures] = []
    for item in raw_features:
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
        playlist_id=result.playlist_id,
        added_to_collection=result.added_to_collection,
        already_in_collection=result.already_in_collection,
        added_to_playlist=result.added_to_playlist,
        already_in_playlist=result.already_in_playlist,
        removed_from_playlist=result.removed_from_playlist,
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


def _cmd_calibrate(args: argparse.Namespace) -> int:
    manifest_path = Path(args.manifest).expanduser()
    if not manifest_path.exists():
        raise ValueError(f"calibration manifest not found: {manifest_path}")
    entries = json.loads(manifest_path.read_text())
    if not isinstance(entries, list):
        raise ValueError("calibration manifest must be a JSON array")

    results: list[dict[str, Any]] = []
    failures = 0
    for entry in entries:
        path = Path(str(entry.get("path", ""))).expanduser()
        if not path.exists():
            results.append({"path": str(path), "ok": False, "error": "missing"})
            failures += 1
            continue
        expected = dict(entry.get("expected") or {})
        _emit("status", message=f"analyzing {path.name}")
        features = analyze_file(path, output_dir=path.parent, use_cache=False)
        drop_cue = next((cue for cue in features.cue_hints if cue.name == "Drop"), None)
        drop_sec = float(drop_cue.seconds) if drop_cue is not None else None
        report = {
            "path": str(path),
            "ok": True,
            "bpm": features.bpm,
            "first_downbeat_sec": features.first_downbeat_sec,
            "segmentation_mode": features.segmentation_mode,
            "segmentation_confidence": features.segmentation_confidence,
            "vocal_class": features.vocal_class,
            "drop_sec": drop_sec,
        }
        report["delta"] = _calibration_delta(expected, features, drop_sec=drop_sec)
        results.append(report)
    _print_json({"ok": True, "results": results, "failures": failures})
    return 0


def _calibration_delta(expected: dict[str, Any], features: TrackFeatures, *, drop_sec: float | None) -> dict[str, Any]:
    delta: dict[str, Any] = {}
    if "bpm" in expected:
        delta["bpm_err"] = round(float(features.bpm) - float(expected["bpm"]), 3)
    if "first_downbeat_sec" in expected:
        delta["downbeat_err_ms"] = round((features.first_downbeat_sec - float(expected["first_downbeat_sec"])) * 1000.0, 1)
    if "drop_sec" in expected and drop_sec is not None:
        bar = 60.0 / features.bpm * 4.0 if features.bpm > 0 else 2.0
        delta["drop_err_bars"] = round((drop_sec - float(expected["drop_sec"])) / max(bar, 1e-6), 3)
    if "vocal_class" in expected:
        delta["vocal_class_match"] = bool(expected["vocal_class"] == features.vocal_class)
    if "segmentation_mode" in expected:
        delta["segmentation_mode_match"] = bool(expected["segmentation_mode"] == features.segmentation_mode)
    return delta


def _cmd_sync_plan(args: argparse.Namespace) -> int:
    url = args.url.strip()
    if not url:
        raise ValueError("url is required")
    state = load_state(url)
    _emit("status", message="Expanding SoundCloud playlist...")
    plan = collect_download_plan([url])
    entries = plan.entries
    current_ids = [entry.id for entry in entries if entry.id]
    diff = diff_track_ids(state.track_ids, current_ids)
    title = plan.title or state.title or "SoundCloud"
    target_dir = Path(state.target_dir) if state.target_dir else derive_target_dir(title)
    suggested_playlist = state.rekordbox_playlist or title
    _print_json(
        {
            "ok": True,
            "url": url,
            "title": title,
            "target_dir": str(target_dir),
            "suggested_playlist": suggested_playlist,
            "rekordbox_playlist_id": state.rekordbox_playlist_id,
            "total_count": len(entries),
            "added": [_entry_to_dict(entry) for entry in entries if entry.id in set(diff.added_ids)],
            "removed_ids": diff.removed_ids,
            "unchanged_count": len(diff.unchanged_ids),
            "is_first_sync": not bool(state.track_ids),
            "entries": [_entry_to_dict(entry) for entry in entries],
        }
    )
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    import datetime as _dt

    payload = _load_payload(args.payload)
    url = str(payload.get("url") or "").strip()
    if not url:
        raise ValueError("payload.url is required")
    playlist_name_override = str(payload.get("playlist_name") or "").strip()
    do_analyze = bool(payload.get("analyze", True))
    do_tags = bool(payload.get("write_tags", True))
    do_push = bool(payload.get("push", True))

    config = load_config()
    state = load_state(url)

    _emit("status", message="Expanding SoundCloud playlist...")
    plan = collect_download_plan([url])
    entries = plan.entries
    title = plan.title or state.title or "SoundCloud"
    target_dir = Path(state.target_dir) if state.target_dir else derive_target_dir(title)
    target_dir = target_dir.expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f".{safe_slug(title)}.archive.txt"

    current_ids = [entry.id for entry in entries if entry.id]
    diff = diff_track_ids(state.track_ids, current_ids)
    _emit(
        "plan",
        title=title,
        target_dir=str(target_dir),
        total=len(entries),
        added=len(diff.added_ids),
        removed=len(diff.removed_ids),
        unchanged=len(diff.unchanged_ids),
    )

    new_entries = [entry for entry in entries if entry.id in set(diff.added_ids)]
    downloaded_paths: list[Path] = []
    failed_downloads: list[dict[str, str]] = []
    if new_entries:
        results = download_entries(
            new_entries,
            output_dir=target_dir,
            workers=config.workers,
            quality=config.quality,
            fragments=config.fragments,
            archive=archive,
            status=lambda message: _emit("status", message=message),
        )
        for result in results:
            if result.ok:
                downloaded_paths.extend(result.output_paths)
            else:
                failed_downloads.append(
                    {
                        "id": result.entry.id or "",
                        "title": result.entry.label,
                        "url": result.entry.url,
                        "error": result.error or "unknown",
                    }
                )
        if failed_downloads:
            _emit(
                "status",
                message=f"failed {len(failed_downloads)} track(s); continuing with the rest",
            )
            failed_path = target_dir / "failed-downloads.txt"
            failed_path.write_text(
                "\n".join(f"{f['id']}\t{f['title']}\t{f['error']}" for f in failed_downloads)
                + "\n"
            )
    else:
        _emit("status", message="No new tracks to download.")

    # Paths covered by the playlist now = current SoundCloud entries' MP3s present on disk
    current_paths = sorted(set(paths_for_entries(target_dir, entries)))
    # Successfully-tracked IDs are the ones we have an MP3 for. Failed downloads are
    # excluded so the next sync retries them instead of treating them as synced.
    tracked_ids = _ids_from_paths(current_paths)
    # Resolve removed track MP3 paths (best-effort: look for any local file with [id] suffix)
    removed_paths = _resolve_removed_paths(target_dir, diff.removed_ids)

    url_by_id = {entry.id: entry.url for entry in entries if entry.id}
    features: list[TrackFeatures] = []
    if (do_analyze or do_push) and current_paths:
        _emit("status", message=f"Analyzing {len(current_paths)} track(s)...")
        raw_features = _analyze_paths_parallel(
            current_paths,
            output_dir=target_dir,
            extract_vocal_stems=config.extract_vocal_stems,
        )
        for item in raw_features:
            source_url = url_by_id.get(item.source_id, item.source_url)
            if source_url:
                item = replace(item, source_url=source_url)
            features.append(item)

    if do_tags and features:
        for item in features:
            write_id3_tags(Path(item.path), item)

    push_summary: dict[str, Any] = {}
    if do_push:
        if rekordbox_running():
            raise RuntimeError("Close Rekordbox before sync (so cues can be written).")
        playlist_name = playlist_name_override or state.rekordbox_playlist or title
        playlist_id = state.rekordbox_playlist_id or None
        result = push_tracks_to_playlist(
            features,
            playlist_name=playlist_name,
            playlist_id=playlist_id,
            create_playlist=True,
            remove_paths=[str(p) for p in removed_paths],
        )
        push_summary = {
            "playlist_name": result.playlist_name,
            "playlist_id": result.playlist_id,
            "added_to_collection": result.added_to_collection,
            "already_in_collection": result.already_in_collection,
            "added_to_playlist": result.added_to_playlist,
            "already_in_playlist": result.already_in_playlist,
            "removed_from_playlist": result.removed_from_playlist,
            "added_cues": result.added_cues,
            "added_loops": result.added_loops,
            "backup_dir": str(result.backup_dir),
        }
        # Persist state with the new playlist binding
        state.rekordbox_playlist = result.playlist_name
        state.rekordbox_playlist_id = result.playlist_id

    # Update sync state regardless of push (so deltas track correctly next time).
    # Only persist IDs we actually have an MP3 for — failed downloads must be
    # retried on the next sync, not silently treated as synced.
    state.url = url
    state.title = title
    state.target_dir = str(target_dir)
    state.track_ids = tracked_ids
    state.last_synced_at = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    save_state(state)

    _emit(
        "done",
        ok=True,
        title=title,
        target_dir=str(target_dir),
        added=len(diff.added_ids),
        removed=len(diff.removed_ids),
        unchanged=len(diff.unchanged_ids),
        analyzed=len(features),
        failed_downloads=failed_downloads,
        push=push_summary,
        features=[features_to_dict(item) for item in features],
    )
    return 0


def _cmd_list_playlist_tracks(args: argparse.Namespace) -> int:
    tracks = list_playlist_tracks(args.playlist_id)
    _print_json(
        {
            "ok": True,
            "tracks": [
                {
                    "content_id": t.content_id,
                    "title": t.title,
                    "artist": t.artist,
                    "folder_path": t.folder_path,
                    "file_exists": t.file_exists,
                    "soundcloud_dl_managed": t.soundcloud_dl_managed,
                }
                for t in tracks
            ],
        }
    )
    return 0


def _cmd_reanalyze_playlist(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    playlist_id = str(payload.get("playlist_id") or "").strip()
    if not playlist_id:
        raise ValueError("payload.playlist_id is required")
    selected_ids: set[str] | None = None
    if payload.get("content_ids"):
        selected_ids = {str(cid) for cid in payload["content_ids"]}
    force_no_cache = bool(payload.get("force_no_cache", True))

    # Fail fast before doing any analysis work — analysis is expensive and the
    # push won't run if Rekordbox is open.
    if rekordbox_running():
        raise RuntimeError("Close Rekordbox before reanalyze (so cues can be written).")

    tracks = list_playlist_tracks(playlist_id)
    if selected_ids is not None:
        tracks = [t for t in tracks if t.content_id in selected_ids]
    if not tracks:
        raise ValueError("no tracks to reanalyze")

    missing = [t for t in tracks if not t.file_exists]
    runnable = [t for t in tracks if t.file_exists]
    if missing:
        _emit(
            "status",
            message=f"skipping {len(missing)} track(s) with missing files",
        )
    if not runnable:
        raise RuntimeError("no playable files for the selected tracks")

    config = load_config()
    paths = [Path(t.folder_path) for t in runnable]
    raw_features = _analyze_paths_parallel(
        paths,
        output_dir=None,  # use each track's parent folder for the analysis cache
        extract_vocal_stems=config.extract_vocal_stems,
        use_cache=not force_no_cache,
    )

    # Tags pass: rewrite ID3 metadata so on-deck displays match the new analysis.
    if config.write_tags:
        for item in raw_features:
            try:
                write_id3_tags(Path(item.path), item)
            except Exception as exc:
                _emit("status", message=f"tag write failed for {Path(item.path).name}: {exc}")

    # Look up the playlist's current name so push_tracks_to_playlist writes back to the same place.
    playlists = list_playlists()
    playlist_match = next((pl for pl in playlists if pl.id == playlist_id), None)
    playlist_name = playlist_match.name if playlist_match else ""

    result = push_tracks_to_playlist(
        raw_features,
        playlist_name=playlist_name,
        playlist_id=playlist_id,
        create_playlist=False,
    )

    _emit(
        "done",
        ok=True,
        playlist_id=result.playlist_id,
        playlist_name=result.playlist_name,
        analyzed=len(raw_features),
        skipped_missing=len(missing),
        added_to_playlist=result.added_to_playlist,
        already_in_playlist=result.already_in_playlist,
        added_cues=result.added_cues,
        added_loops=result.added_loops,
        backup_dir=str(result.backup_dir),
        missing=[
            {"content_id": t.content_id, "title": t.title, "folder_path": t.folder_path}
            for t in missing
        ],
    )
    return 0


def _ids_from_paths(paths: list[Path]) -> list[str]:
    import re as _re

    ids: list[str] = []
    for path in paths:
        match = _re.search(r"\[(\d{5,})\]\.mp3$", path.name)
        if match:
            ids.append(match.group(1))
    return ids


def _resolve_removed_paths(target_dir: Path, removed_ids: list[str]) -> list[Path]:
    if not removed_ids:
        return []
    target_dir = target_dir.expanduser()
    out: list[Path] = []
    for sid in removed_ids:
        suffix = f"[{sid}].mp3"
        for path in target_dir.glob("*.mp3"):
            if path.name.endswith(suffix):
                out.append(path.resolve())
    return out


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
