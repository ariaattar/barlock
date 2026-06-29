from __future__ import annotations

import datetime as dt
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from pyrekordbox import Rekordbox6Database, RekordboxXml
from pyrekordbox.db6 import tables
from pyrekordbox.utils import get_rekordbox_pid

from .audio_features import TrackFeatures

REKORDBOX_DIR = Path.home() / "Library" / "Pioneer" / "rekordbox"
AUTO_CUE_NAMES = {
    "Intro",
    "Phrase 16",
    "Phrase 32",
    "Build",
    "Drop",
    "Intro Loop",
    "Exit Loop",
    "Breakdown",
    "Last Drop",
    "Outro",
}
HOT_CUE_KINDS = (1, 2, 3, 5, 6, 7, 8, 9)
RESERVED_EXTERNAL_SLOTS = {5, 6}  # zero-based hot cue slots only written on soundcloud-dl managed tracks


@dataclass(frozen=True)
class PlaylistInfo:
    id: str
    name: str
    path: str
    is_folder: bool
    song_count: int = 0


@dataclass(frozen=True)
class PushResult:
    playlist_name: str
    playlist_id: str
    added_to_collection: int
    already_in_collection: int
    added_to_playlist: int
    already_in_playlist: int
    removed_from_playlist: int
    added_cues: int
    skipped_cues: int
    added_loops: int
    skipped_loops: int
    backup_dir: Path


def rekordbox_running() -> bool:
    return bool(get_rekordbox_pid())


def close_rekordbox(*, timeout_sec: float = 20.0) -> bool:
    if not rekordbox_running():
        return True

    try:
        subprocess.run(
            ["osascript", "-e", 'tell application "rekordbox" to quit'],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline:
        if not rekordbox_running():
            return True
        time.sleep(0.5)
    return not rekordbox_running()


def backup_rekordbox() -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_dir = REKORDBOX_DIR / "backups" / f"soundcloud-dl-{stamp}"
    backup_dir.mkdir(parents=True, exist_ok=True)
    for name in ["master.db", "master.db-wal", "master.db-shm", "masterPlaylists6.xml"]:
        src = REKORDBOX_DIR / name
        if src.exists():
            shutil.copy2(src, backup_dir / name)
    return backup_dir


def list_playlists() -> list[PlaylistInfo]:
    db = Rekordbox6Database()
    try:
        playlists = list(db.get_playlist())
        by_parent: dict[str, list] = {}
        by_id = {str(pl.ID): pl for pl in playlists}
        for pl in playlists:
            by_parent.setdefault(str(pl.ParentID), []).append(pl)

        def path_for(pl) -> str:
            names = [pl.Name]
            parent_id = str(pl.ParentID)
            while parent_id in by_id:
                parent = by_id[parent_id]
                names.append(parent.Name)
                parent_id = str(parent.ParentID)
            return " / ".join(reversed(names))

        out: list[PlaylistInfo] = []
        for pl in playlists:
            if pl.is_smart_playlist:
                continue
            out.append(
                PlaylistInfo(
                    id=str(pl.ID),
                    name=pl.Name,
                    path=path_for(pl),
                    is_folder=bool(pl.is_folder),
                    song_count=len(pl.Songs) if not pl.is_folder else 0,
                )
            )
        return sorted(out, key=lambda item: (item.is_folder, item.path.lower()))
    finally:
        db.close()


def push_tracks_to_playlist(
    features: list[TrackFeatures],
    *,
    playlist_name: str,
    create_playlist: bool,
    playlist_id: str | None = None,
    remove_paths: list[str] | None = None,
) -> PushResult:
    if rekordbox_running():
        raise RuntimeError("Close Rekordbox before direct playlist push.")
    if not features and not remove_paths:
        raise ValueError("no tracks to push")

    backup_dir = backup_rekordbox()
    db = Rekordbox6Database()
    added_collection = 0
    existing_collection = 0
    added_playlist = 0
    existing_playlist = 0
    removed_playlist = 0
    added_cues = 0
    skipped_cues = 0
    added_loops = 0
    skipped_loops = 0
    try:
        playlist = db.get_playlist(ID=playlist_id) if playlist_id else _find_playlist(db, playlist_name)
        if playlist is None:
            if not create_playlist:
                raise ValueError(f"playlist not found: {playlist_name}")
            playlist = db.create_playlist(playlist_name)
            db.flush()
        if playlist.is_folder:
            raise ValueError(f"'{playlist_name}' is a folder, not a playlist")

        existing_playlist_ids = {
            str(song.ContentID)
            for song in db.get_playlist_songs(PlaylistID=playlist.ID).all()
        }

        if remove_paths:
            removed_playlist = _remove_paths_from_playlist(db, playlist, remove_paths)
            _safe_flush(db)
            existing_playlist_ids = {
                str(song.ContentID)
                for song in db.get_playlist_songs(PlaylistID=playlist.ID).all()
            }

        for item in features:
            content, created = _ensure_content(db, item)
            if created:
                added_collection += 1
            else:
                existing_collection += 1
            _apply_metadata(db, content, item)
            _safe_flush(db)
            cue_added, cue_skipped, loop_added, loop_skipped = _sync_cue_hints(db, content, item)
            added_cues += cue_added
            skipped_cues += cue_skipped
            added_loops += loop_added
            skipped_loops += loop_skipped
            _safe_flush(db)
            if str(content.ID) in existing_playlist_ids:
                existing_playlist += 1
                continue
            db.add_to_playlist(playlist, content)
            existing_playlist_ids.add(str(content.ID))
            added_playlist += 1
            _safe_flush(db)

        db.commit()
        return PushResult(
            playlist_name=playlist.Name,
            playlist_id=str(playlist.ID),
            added_to_collection=added_collection,
            already_in_collection=existing_collection,
            added_to_playlist=added_playlist,
            already_in_playlist=existing_playlist,
            removed_from_playlist=removed_playlist,
            added_cues=added_cues,
            skipped_cues=skipped_cues,
            added_loops=added_loops,
            skipped_loops=skipped_loops,
            backup_dir=backup_dir,
        )
    except Exception:
        db.session.rollback()
        raise
    finally:
        db.close()


def _remove_paths_from_playlist(db: Rekordbox6Database, playlist, remove_paths: list[str]) -> int:
    targets = {str(Path(p).resolve()) for p in remove_paths if p}
    if not targets:
        return 0
    removed = 0
    for song in list(db.get_playlist_songs(PlaylistID=playlist.ID).all()):
        content = song.Content
        if content is None:
            continue
        path = str(content.FolderPath) if content.FolderPath else ""
        if path in targets:
            db.delete(song)
            removed += 1
    return removed


def write_m3u(features: list[TrackFeatures], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["#EXTM3U"]
    for item in features:
        title = f"{item.artist} - {item.title}" if item.artist else item.title
        lines.append(f"#EXTINF:{int(round(item.duration_sec))},{title}")
        lines.append(str(Path(item.path).resolve()))
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


def write_rekordbox_xml(features: list[TrackFeatures], out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    xml = RekordboxXml(name="rekordbox", version="6.0.0", company="Pioneer DJ")
    for idx, item in enumerate(features, start=1):
        path = Path(item.path)
        track = xml.add_track(
            location=str(path.resolve()),
            TrackID=idx,
            Name=item.title or path.stem,
            Artist=item.artist,
            TotalTime=int(round(item.duration_sec)),
            AverageBpm=round(item.bpm, 2),
            SampleRate=item.sample_rate,
            BitRate=320,
            Kind=_kind_for_suffix(path.suffix),
            Comments=_comment(item),
            Tonality=item.camelot_key or item.musical_key,
        )
        if item.bpm > 0:
            track.add_tempo(
                Inizio=round(item.first_downbeat_sec, 3),
                Bpm=round(item.bpm, 2),
                Metro="4/4",
                Battito=1,
            )
        for cue in item.cue_hints:
            mark_type = "loop" if cue.kind == "loop" and cue.end_seconds is not None else "cue"
            track.add_mark(
                Name=cue.name,
                Type=mark_type,
                Start=round(cue.seconds, 3),
                End=round(cue.end_seconds, 3) if mark_type == "loop" else None,
                Num=cue.hotcue_slot if cue.hotcue_slot is not None else -1,
            )
    xml.save(str(out_path))
    return out_path


def doctor(output_dir: Path) -> dict:
    db = Rekordbox6Database()
    try:
        contents = list(db.get_content())
        local = [c for c in contents if c.FolderPath and str(c.FolderPath).startswith("/")]
        missing = [c.FolderPath for c in local if not Path(c.FolderPath).exists()]
        output_files = [path for path in output_dir.expanduser().glob("*.mp3")]
        collection_paths = {str(c.FolderPath) for c in contents if c.FolderPath}
        unimported = [str(path) for path in output_files if str(path.resolve()) not in collection_paths]
        return {
            "collection_tracks": len(contents),
            "local_tracks": len(local),
            "missing_files": missing,
            "unimported_output_files": unimported,
            "rekordbox_running": rekordbox_running(),
        }
    finally:
        db.close()


def _find_playlist(db: Rekordbox6Database, name: str):
    matches = [pl for pl in db.get_playlist(Name=name).all() if pl.is_playlist]
    return matches[0] if matches else None


def _ensure_content(db: Rekordbox6Database, item: TrackFeatures):
    path = str(Path(item.path).resolve())
    existing = db.get_content(FolderPath=path).first()
    if existing is not None:
        return existing, False
    content = db.add_content(
        path,
        Title=item.title or Path(path).stem,
        BPM=int(round(item.bpm * 100)) if item.bpm > 0 else None,
        Length=int(round(item.duration_sec)) if item.duration_sec else None,
        BitRate=320,
        SampleRate=item.sample_rate,
        Commnt=_comment(item),
        Analysed=0,
    )
    return content, True


def _apply_metadata(db: Rekordbox6Database, content, item: TrackFeatures) -> None:
    content.Title = item.title or content.Title
    content.BPM = int(round(item.bpm * 100)) if item.bpm > 0 else content.BPM
    content.Length = int(round(item.duration_sec)) if item.duration_sec else content.Length
    content.SampleRate = item.sample_rate or content.SampleRate
    content.Commnt = _comment(item)
    if item.artist:
        artist = _get_or_add_artist(db, item.artist)
        content.ArtistID = artist.ID
    if item.rekordbox_key:
        key = db.get_key(ScaleName=item.rekordbox_key).first()
        if key is not None:
            content.KeyID = key.ID


def _sync_cue_hints(db: Rekordbox6Database, content, item: TrackFeatures) -> tuple[int, int, int, int]:
    existing = list(db.get_cue(ContentID=content.ID).all())
    removed_cues = 0
    is_managed = _is_soundcloud_dl_content(content)
    if is_managed:
        existing, removed_cues = _remove_generated_cues(db, existing)
    existing_hotcue_kinds = {int(cue.Kind) for cue in existing if cue.Kind and int(cue.Kind) > 0}
    existing_memory_ms = [
        int(cue.InMsec)
        for cue in existing
        if int(cue.Kind or 0) == 0 and cue.InMsec is not None
    ]

    added_cues = 0
    skipped_cues = 0
    added_loops = 0
    skipped_loops = 0
    for hint in item.cue_hints:
        is_loop = _is_loop_hint(hint)
        kind = _cue_kind(hint)
        if kind is None:
            if is_loop:
                skipped_loops += 1
            else:
                skipped_cues += 1
            continue
        # Slots 5 and 6 (Breakdown / Last Drop) are only written on soundcloud-dl-managed tracks
        # so we never overwrite a user's manually-placed pads on external tracks.
        if hint.hotcue_slot in RESERVED_EXTERNAL_SLOTS and not is_managed:
            skipped_cues += 1
            continue
        in_msec = max(0, int(round(hint.seconds * 1000.0)))
        if kind > 0 and kind in existing_hotcue_kinds:
            if is_loop:
                skipped_loops += 1
            else:
                skipped_cues += 1
            continue
        if kind == 0 and any(abs(in_msec - current) <= 500 for current in existing_memory_ms):
            if is_loop:
                skipped_loops += 1
            else:
                skipped_cues += 1
            continue
        out_msec = (
            max(in_msec + 1, int(round(hint.end_seconds * 1000.0)))
            if is_loop and hint.end_seconds is not None
            else -1
        )

        cue = tables.DjmdCue.create(
            ID=str(db.generate_unused_id(tables.DjmdCue, is_28_bit=False)),
            ContentID=str(content.ID),
            ContentUUID=str(content.UUID),
            InMsec=in_msec,
            OutMsec=out_msec,
            Kind=kind,
            CueMicrosec=0 if is_loop else None,
            Color=255 if is_loop else -1,
            ColorTableIndex=0 if is_loop else None,
            ActiveLoop=0 if is_loop else None,
            Comment=hint.name,
            BeatLoopSize=_beat_loop_size(hint) if is_loop else None,
        )
        db.add(cue)
        if kind > 0:
            existing_hotcue_kinds.add(kind)
        else:
            existing_memory_ms.append(in_msec)
        if is_loop:
            added_loops += 1
        else:
            added_cues += 1

    total_changed = removed_cues + added_cues + added_loops
    if total_changed:
        content.HotCueAutoLoad = "on"
        try:
            current = int(content.CueUpdated or 0)
        except (TypeError, ValueError):
            current = 0
        content.CueUpdated = str(max(current + 1, len(existing) + total_changed))
    return added_cues, skipped_cues, added_loops, skipped_loops


def _remove_generated_cues(db: Rekordbox6Database, existing: list) -> tuple[list, int]:
    kept = []
    removed = 0
    for cue in existing:
        if (cue.Comment or "") in AUTO_CUE_NAMES:
            db.delete(cue)
            removed += 1
        else:
            kept.append(cue)
    if removed:
        # Flush deletes before any inserts. Legacy Rekordbox rows can store ID columns
        # as int while we write str — sqlalchemy's _sort_states cannot sort a mixed-type
        # pending set, so we keep each flush group homogeneous.
        _safe_flush(db)
    return kept, removed


def _safe_flush(db) -> None:
    flush = getattr(db, "flush", None)
    if not callable(flush):
        return
    try:
        flush()
    except Exception:
        # The session may be a test fake without a real flush; ignore.
        pass


def _is_soundcloud_dl_content(content) -> bool:
    return "soundcloud-dl" in str(getattr(content, "Commnt", "") or "")


def _cue_kind(hint) -> int | None:
    if hint.hotcue_slot is None:
        return 0
    slot = int(hint.hotcue_slot)
    if slot < 0 or slot >= len(HOT_CUE_KINDS):
        return None
    return HOT_CUE_KINDS[slot]


def _is_loop_hint(hint) -> bool:
    return hint.kind == "loop" and hint.end_seconds is not None and hint.end_seconds > hint.seconds


def _beat_loop_size(hint) -> int | None:
    if hint.loop_beats is None:
        return None
    beats = int(hint.loop_beats)
    if beats <= 0:
        return None
    return beats * 65536 + 1


def _get_or_add_artist(db: Rekordbox6Database, name: str):
    artist = db.get_artist(Name=name).first()
    if artist is not None:
        return artist
    return db.add_artist(name)


def _comment(item: TrackFeatures) -> str:
    parts = ["soundcloud-dl"]
    if item.source_url:
        parts.append(item.source_url)
    if item.camelot_key or item.musical_key:
        parts.append(f"{item.camelot_key or item.musical_key}")
    if item.energy:
        parts.append(f"energy {item.energy}/10")
    if item.vocal_class and item.vocal_class != "unknown":
        parts.append(item.vocal_class)
    drop = next((cue for cue in item.cue_hints if cue.name == "Drop"), None)
    if drop is not None:
        parts.append(f"drop @ {_format_mmss(drop.seconds)}")
    if not item.tempo_stable:
        parts.append("tempo: variable")
    return " | ".join(parts)


def _format_mmss(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    return f"{seconds // 60}:{seconds % 60:02d}"


def _kind_for_suffix(suffix: str) -> str:
    return {
        ".mp3": "MP3 File",
        ".wav": "WAV File",
        ".aiff": "AIFF File",
        ".aif": "AIFF File",
        ".flac": "FLAC File",
        ".m4a": "M4A File",
    }.get(suffix.lower(), "Audio File")
