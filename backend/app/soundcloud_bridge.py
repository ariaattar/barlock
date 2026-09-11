from __future__ import annotations

import argparse
import json
import os
import sys
import threading
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any, Callable

from .audio_features import TrackFeatures, analyze_file, analyze_one_worker, audio_files, write_id3_tags
from .editor_service import (
    adopt_cues,
    apply_draft,
    load_editor,
    prepare_audition,
    preview_apply,
    rebase_draft,
    reset_draft,
    save_draft,
)
from .rekordbox_sync import (
    close_rekordbox,
    doctor,
    list_playlist_tracks,
    list_playlists,
    list_usb_devices,
    open_rekordbox,
    push_tracks_to_playlist,
    rekordbox_waveform,
    remove_generated_cues_from_playlist,
    repair_generated_active_loops,
    repair_generated_off_grid_loops,
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
    import_local_replacement,
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

    replacement_parser = subparsers.add_parser("import-replacement")
    replacement_parser.add_argument("--payload", default="-")
    replacement_parser.set_defaults(handler=_cmd_import_replacement)

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
    subparsers.add_parser("usb-status").set_defaults(handler=_cmd_usb_status)
    subparsers.add_parser("close-rekordbox").set_defaults(handler=_cmd_close_rekordbox)
    subparsers.add_parser("open-rekordbox").set_defaults(handler=_cmd_open_rekordbox)
    subparsers.add_parser("list-playlists").set_defaults(handler=_cmd_list_playlists)

    push_parser = subparsers.add_parser("push")
    push_parser.add_argument("--payload", default="-")
    push_parser.set_defaults(handler=_cmd_push)

    doctor_parser = subparsers.add_parser("doctor")
    doctor_parser.add_argument("--output-dir")
    doctor_parser.set_defaults(handler=_cmd_doctor)

    subparsers.add_parser("repair-generated-active-loops").set_defaults(handler=_cmd_repair_generated_active_loops)
    subparsers.add_parser("repair-generated-off-grid-loops").set_defaults(
        handler=_cmd_repair_generated_off_grid_loops
    )
    remove_cues_parser = subparsers.add_parser("remove-generated-cues")
    remove_cues_parser.add_argument("--playlist-id", required=True)
    remove_cues_parser.set_defaults(handler=_cmd_remove_generated_cues)

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

    waveform_parser = subparsers.add_parser("rekordbox-waveform")
    waveform_parser.add_argument("--content-id", required=True)
    waveform_parser.add_argument("--max-points", type=int, default=2400)
    waveform_parser.set_defaults(handler=_cmd_rekordbox_waveform)

    reanalyze_parser = subparsers.add_parser("reanalyze-playlist")
    reanalyze_parser.add_argument("--payload", default="-")
    reanalyze_parser.set_defaults(handler=_cmd_reanalyze_playlist)

    editor_load_parser = subparsers.add_parser("editor-load")
    editor_load_parser.add_argument("--payload", default="-")
    editor_load_parser.set_defaults(handler=_cmd_editor_load)

    editor_save_parser = subparsers.add_parser("editor-save-draft")
    editor_save_parser.add_argument("--payload", default="-")
    editor_save_parser.set_defaults(handler=_cmd_editor_save_draft)

    editor_audition_parser = subparsers.add_parser("editor-prepare-audition")
    editor_audition_parser.add_argument("--payload", default="-")
    editor_audition_parser.set_defaults(handler=_cmd_editor_prepare_audition)

    editor_preview_parser = subparsers.add_parser("editor-preview-apply")
    editor_preview_parser.add_argument("--payload", default="-")
    editor_preview_parser.set_defaults(handler=_cmd_editor_preview_apply)

    editor_apply_parser = subparsers.add_parser("editor-apply")
    editor_apply_parser.add_argument("--payload", default="-")
    editor_apply_parser.set_defaults(handler=_cmd_editor_apply)

    editor_adopt_parser = subparsers.add_parser("editor-adopt-cues")
    editor_adopt_parser.add_argument("--payload", default="-")
    editor_adopt_parser.set_defaults(handler=_cmd_editor_adopt_cues)

    editor_reset_parser = subparsers.add_parser("editor-reset-auto")
    editor_reset_parser.add_argument("--payload", default="-")
    editor_reset_parser.set_defaults(handler=_cmd_editor_reset_auto)

    editor_rebase_parser = subparsers.add_parser("editor-rebase")
    editor_rebase_parser.add_argument("--payload", default="-")
    editor_rebase_parser.set_defaults(handler=_cmd_editor_rebase)

    return parser


def _default_analyze_workers() -> int:
    return max(1, min(os.cpu_count() or 4, 4))


def _packaged_runtime() -> bool:
    return bool(getattr(sys, "frozen", False))


def _analyze_paths_serial(
    paths: list[Path],
    *,
    output_dir: Path | None,
    extract_vocal_stems: bool,
    use_cache: bool,
) -> list[TrackFeatures]:
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


def _analyze_paths_parallel(
    paths: list[Path],
    *,
    output_dir: Path | None,
    extract_vocal_stems: bool,
    use_cache: bool = True,
    workers: int | None = None,
) -> list[TrackFeatures]:
    """Analyze N audio paths concurrently, preserving input order.

    Source runs use processes so CPU-heavy feature extraction can scale across
    cores. Frozen desktop builds use a small thread pool because spawning the
    bundled NumPy runtime is unstable on macOS. Small batches stay serial.
    """
    if not paths:
        return []

    worker_count = workers if workers is not None else _default_analyze_workers()
    worker_count = max(1, min(worker_count, len(paths)))

    # For tiny batches the executor overhead dwarfs the parallel savings.
    if worker_count == 1 or len(paths) < 3:
        return _analyze_paths_serial(
            paths,
            output_dir=output_dir,
            extract_vocal_stems=extract_vocal_stems,
            use_cache=use_cache,
        )

    packaged = _packaged_runtime()
    executor_type = ThreadPoolExecutor if packaged else ProcessPoolExecutor
    if packaged:
        worker_count = min(worker_count, 2)

    total = len(paths)
    output_dir_str = str(output_dir) if output_dir is not None else ""
    work_items = [
        (str(path), output_dir_str, use_cache, extract_vocal_stems)
        for path in paths
    ]
    results_by_index: dict[int, TrackFeatures] = {}
    finished = 0
    lock = threading.Lock()

    def emit_progress(track_name: str = "") -> None:
        with lock:
            tail = f" ({track_name})" if track_name else ""
            _emit(
                "status",
                message=f"[{finished}/{total} done] analyzing{tail}",
            )

    try:
        with executor_type(max_workers=worker_count) as executor:
            futures = {}
            for index, item in enumerate(work_items):
                future = executor.submit(analyze_one_worker, item)
                futures[future] = (index, Path(item[0]).name)
            emit_progress()
            for future in as_completed(futures):
                index, name = futures[future]
                try:
                    features = future.result()
                except BrokenProcessPool:
                    raise
                except Exception as exc:
                    raise RuntimeError(f"analysis failed for {name}: {exc}") from exc
                results_by_index[index] = features
                with lock:
                    finished += 1
                emit_progress(name)
    except BrokenProcessPool:
        _emit(
            "status",
            message="Parallel analyzer stopped unexpectedly; retrying safely...",
        )
        return _analyze_paths_serial(
            paths,
            output_dir=output_dir,
            extract_vocal_stems=extract_vocal_stems,
            use_cache=use_cache,
        )

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
        download_outcomes=[_download_outcome_to_dict(result) for result in results],
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


def _cmd_import_replacement(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    source_value = str(payload.get("source") or "").strip()
    output_value = str(payload.get("output_dir") or "").strip()
    if not source_value or not output_value:
        raise ValueError("payload.source and payload.output_dir are required")
    output_dir = Path(output_value)
    path = import_local_replacement(
        Path(source_value),
        output_dir=output_dir,
        track_id=str(payload.get("track_id") or ""),
        title=str(payload.get("title") or "SoundCloud track"),
    )
    _emit("status", message=f"Analyzing replacement {path.name}")
    config = load_config()
    features = analyze_file(
        path,
        output_dir=output_dir,
        extract_vocal_stems=config.extract_vocal_stems,
    )
    write_id3_tags(path, features)
    _emit("done", ok=True, path=str(path), features=features_to_dict(features))
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


def _cmd_usb_status(_args: argparse.Namespace) -> int:
    _print_json({"ok": True, "devices": list_usb_devices()})
    return 0


def _cmd_close_rekordbox(_args: argparse.Namespace) -> int:
    _print_json({"ok": True, "closed": close_rekordbox()})
    return 0


def _cmd_open_rekordbox(_args: argparse.Namespace) -> int:
    _print_json({"ok": True, "opened": open_rekordbox()})
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
        cue_mode=str(payload.get("cue_mode") or "off"),
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
        pending_grid_alignment=getattr(result, "pending_grid_alignment", 0),
    )
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    config = load_config()
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else config.output_path
    _print_json({"ok": True, "doctor": doctor(output_dir)})
    return 0


def _cmd_repair_generated_active_loops(_args: argparse.Namespace) -> int:
    result = repair_generated_active_loops()
    _print_json(
        {
            "ok": True,
            "repaired": len(result.repaired),
            "tracks": [
                {
                    "title": issue.title,
                    "artist": issue.artist,
                    "cue_name": issue.cue_name,
                    "in_msec": issue.in_msec,
                }
                for issue in result.repaired
            ],
            "backup_dir": str(result.backup_dir) if result.backup_dir else "",
        }
    )
    return 0


def _cmd_repair_generated_off_grid_loops(_args: argparse.Namespace) -> int:
    result = repair_generated_off_grid_loops()
    _print_json(
        {
            "ok": True,
            "repaired": len(result.repaired),
            "tracks": [
                {
                    "title": issue.title,
                    "artist": issue.artist,
                    "cue_name": issue.cue_name,
                    "old_in_msec": issue.old_in_msec,
                    "new_in_msec": issue.new_in_msec,
                    "loop_beats": issue.loop_beats,
                }
                for issue in result.repaired
            ],
            "backup_dir": str(result.backup_dir) if result.backup_dir else "",
        }
    )
    return 0


def _cmd_remove_generated_cues(args: argparse.Namespace) -> int:
    result = remove_generated_cues_from_playlist(str(args.playlist_id))
    _print_json(
        {
            "ok": True,
            "playlist_name": result.playlist_name,
            "removed_cues": result.removed_cues,
            "affected_tracks": result.affected_tracks,
            "backup_dir": str(result.backup_dir) if result.backup_dir else "",
        }
    )
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


def _sync_diff(state: SyncState, entries: list[DownloadEntry], target_dir: Path):
    current_ids = [entry.id for entry in entries if entry.id]
    current_id_set = set(current_ids)
    local_ids = {
        entry.id for entry in entries
        if entry.id and paths_for_entries(target_dir, [entry])
    }
    # Retain upstream removals in history, but retry current tracks whose local
    # files were deleted or are absent from a newly selected destination.
    known_ids = [
        track_id for track_id in state.track_ids
        if track_id not in current_id_set or track_id in local_ids
    ]
    return diff_track_ids(known_ids, current_ids)


def _cmd_sync_plan(args: argparse.Namespace) -> int:
    url = args.url.strip()
    if not url:
        raise ValueError("url is required")
    state = load_state(url)
    _emit("status", message="Expanding SoundCloud playlist...")
    plan = collect_download_plan([url], status=lambda message: _emit("status", message=message))
    entries = plan.entries
    title = plan.title or state.title or "SoundCloud"
    target_dir = Path(state.target_dir) if state.target_dir else derive_target_dir(title)
    diff = _sync_diff(state, entries, target_dir)
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
            "is_first_sync": not bool(state.track_ids or state.protected_ids),
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
    target_dir_override = str(payload.get("target_dir") or "").strip()
    do_analyze = bool(payload.get("analyze", True))
    do_tags = bool(payload.get("write_tags", True))
    do_push = bool(payload.get("push", True))
    cue_mode = str(payload.get("cue_mode") or "off")
    if cue_mode not in {"off", "fill"}:
        raise ValueError(f"invalid cue mode: {cue_mode}")

    config = load_config()
    state = load_state(url)

    _emit("status", message="Expanding SoundCloud playlist...")
    plan = collect_download_plan([url], status=lambda message: _emit("status", message=message))
    entries = plan.entries
    title = plan.title or state.title or "SoundCloud"
    if target_dir_override:
        target_dir = Path(target_dir_override)
        if not target_dir.is_absolute():
            target_dir = Path.home() / "Downloads" / target_dir
    else:
        target_dir = Path(state.target_dir) if state.target_dir else derive_target_dir(title)
    target_dir = target_dir.expanduser()
    target_dir.mkdir(parents=True, exist_ok=True)
    archive = target_dir / f".{safe_slug(title)}.archive.txt"

    current_ids = [entry.id for entry in entries if entry.id]
    diff = _sync_diff(state, entries, target_dir)
    _emit(
        "plan",
        title=title,
        target_dir=str(target_dir),
        total=len(entries),
        added=len(diff.added_ids),
        removed=0,
        retained=len(diff.removed_ids),
        unchanged=len(diff.unchanged_ids),
    )

    candidate_new_entries = [entry for entry in entries if entry.id in set(diff.added_ids)]
    new_entries = [
        entry for entry in candidate_new_entries
        if not paths_for_entries(target_dir, [entry])
    ]
    existing_new_count = len(candidate_new_entries) - len(new_entries)
    if existing_new_count:
        _emit("status", message=f"Using {existing_new_count} existing replacement file(s).")
    downloaded_paths: list[Path] = []
    download_results: list[DownloadResult] = []
    failed_downloads: list[dict[str, str]] = []
    protected_ids = set(state.protected_ids)
    if new_entries:
        download_results = download_entries(
            new_entries,
            output_dir=target_dir,
            workers=config.workers,
            quality=config.quality,
            fragments=config.fragments,
            archive=archive,
            status=lambda message: _emit("status", message=message),
        )
        for result in download_results:
            if result.ok:
                downloaded_paths.extend(result.output_paths)
            else:
                failed_downloads.append(
                    {
                        "id": result.entry.id or "",
                        "title": result.entry.label,
                        "url": result.entry.url,
                        "error": result.error or "unknown",
                        "reason": result.reason or "download",
                    }
                )
                if result.reason == "protected" and result.entry.id:
                    protected_ids.add(result.entry.id)
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

    # Persist download evidence in the UI before native analysis can fail.
    _emit("download-results", download_outcomes=[
        _download_outcome_to_dict(result) for result in download_results
    ])

    # Paths covered by the playlist now = current SoundCloud entries' MP3s present on disk
    current_paths = sorted(set(paths_for_entries(target_dir, entries)))
    # Successfully-tracked IDs are the ones we have an MP3 for. Failed downloads are
    # excluded so the next sync retries them instead of treating them as synced.
    tracked_ids = _ids_from_paths(current_paths)
    tracked_id_set = set(tracked_ids)
    if diff.removed_ids:
        _emit("status", message=f"Keeping {len(diff.removed_ids)} previously saved track(s) no longer listed on SoundCloud.")

    entry_by_id = {entry.id: entry for entry in entries if entry.id}
    features: list[TrackFeatures] = []
    if (do_analyze or do_push) and current_paths:
        _emit("status", message=f"Analyzing {len(current_paths)} track(s)...")
        raw_features = _analyze_paths_parallel(
            current_paths,
            output_dir=target_dir,
            extract_vocal_stems=config.extract_vocal_stems,
        )
        for item in raw_features:
            entry = entry_by_id.get(item.source_id)
            source_url = entry.url if entry else item.source_url
            if source_url:
                item = replace(item, source_url=source_url)
            if entry:
                # Repair numeric titles left by older fallback downloads
                # without changing meaningful existing titles.
                if entry.title and (not item.title or item.title == entry.id):
                    item = replace(item, title=entry.title, artist=entry.artist or item.artist)
                elif entry.artist and not item.artist:
                    item = replace(item, artist=entry.artist)
            features.append(item)

    if do_tags and features:
        for item in features:
            write_id3_tags(Path(item.path), item)

    push_summary: dict[str, Any] = {}
    if do_push and features:
        if rekordbox_running():
            raise RuntimeError("Close Rekordbox before sync so the playlist can be updated.")
        playlist_name = playlist_name_override or state.rekordbox_playlist or title
        playlist_id = state.rekordbox_playlist_id or None
        result = push_tracks_to_playlist(
            features,
            playlist_name=playlist_name,
            playlist_id=playlist_id,
            create_playlist=True,
            cue_mode=cue_mode,
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
            "pending_grid_alignment": getattr(result, "pending_grid_alignment", 0),
        }
        # Persist state with the new playlist binding
        state.rekordbox_playlist = result.playlist_name
        state.rekordbox_playlist_id = result.playlist_id

    # Update sync state regardless of push (so deltas track correctly next time).
    # Keep previously downloaded IDs even after an unlike or takedown. Add only
    # successful local downloads, so new failures remain retryable.
    state.url = url
    state.title = title
    state.target_dir = str(target_dir)
    state.track_ids = list(dict.fromkeys([*state.track_ids, *tracked_ids]))
    state.protected_ids = [
        track_id for track_id in current_ids
        if track_id in protected_ids and track_id not in tracked_id_set
    ]
    state.last_synced_at = _dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"
    save_state(state)

    _emit(
        "done",
        ok=True,
        title=title,
        target_dir=str(target_dir),
        added=len(diff.added_ids),
        removed=0,
        retained=len(diff.removed_ids),
        unchanged=len(diff.unchanged_ids),
        analyzed=len(features),
        failed_downloads=failed_downloads,
        download_outcomes=[
            _download_outcome_to_dict(result) for result in download_results
        ],
        protected_ids=state.protected_ids,
        push=push_summary,
        paths=[str(path) for path in current_paths],
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


def _cmd_rekordbox_waveform(args: argparse.Namespace) -> int:
    result = rekordbox_waveform(str(args.content_id), max_points=int(args.max_points))
    _print_json({"ok": True, "waveform": asdict(result)})
    return 0


def _cmd_editor_load(args: argparse.Namespace) -> int:
    payload = _load_payload(args.payload)
    result = load_editor(
        content_id=str(payload.get("content_id") or ""),
        path=str(payload.get("path") or ""),
    )
    _print_json({"ok": True, "editor": result})
    return 0


def _cmd_editor_save_draft(args: argparse.Namespace) -> int:
    result = save_draft(_load_payload(args.payload))
    _print_json({"ok": True, "draft": result})
    return 0


def _cmd_editor_prepare_audition(args: argparse.Namespace) -> int:
    result = prepare_audition(_load_payload(args.payload))
    _print_json({"ok": True, "audition": result})
    return 0


def _cmd_editor_preview_apply(args: argparse.Namespace) -> int:
    result = preview_apply(_load_payload(args.payload))
    _print_json({"ok": True, "preview": result})
    return 0


def _cmd_editor_apply(args: argparse.Namespace) -> int:
    result = apply_draft(_load_payload(args.payload))
    _emit("done", ok=True, **result)
    return 0


def _cmd_editor_adopt_cues(args: argparse.Namespace) -> int:
    result = adopt_cues(_load_payload(args.payload))
    _print_json({"ok": True, **result})
    return 0


def _cmd_editor_reset_auto(args: argparse.Namespace) -> int:
    result = reset_draft(_load_payload(args.payload))
    _print_json({"ok": True, "draft": result})
    return 0


def _cmd_editor_rebase(args: argparse.Namespace) -> int:
    result = rebase_draft(_load_payload(args.payload))
    _print_json({"ok": True, "editor": result})
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
    cue_mode = str(payload.get("cue_mode") or "off")
    if cue_mode not in {"off", "fill"}:
        raise ValueError(f"invalid cue mode: {cue_mode}")

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
        cue_mode=cue_mode,
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
        pending_grid_alignment=getattr(result, "pending_grid_alignment", 0),
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
        **({"artist": entry.artist} if entry.artist else {}),
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
                artist=str(item.get("artist") or "") or None,
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
        "id": result.entry.id or "",
        "title": result.entry.title or result.entry.label,
        "url": result.entry.url,
        "label": result.entry.label,
        "error": result.error or "",
        "reason": result.reason or "download",
    }


def _download_outcome_to_dict(result: DownloadResult) -> dict[str, Any]:
    if result.ok and result.download_method == "klickaud":
        outcome = "fallback_succeeded"
    elif result.ok:
        outcome = "primary_succeeded"
    else:
        outcome = "failed"
    return {
        "id": result.entry.id or "",
        "title": result.entry.title or result.entry.label,
        "url": result.entry.url,
        "outcome": outcome,
        "download_method": result.download_method if result.ok else "",
        "fallback_attempted": result.fallback_attempted,
        "primary_error": result.primary_error or "",
        "error": result.error or "",
        "paths": [str(path) for path in result.output_paths],
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
