"""Rekordbox XML export.

Writes Pioneer's collection XML so that tracks, beat grids, hot cues, and
memory cues round-trip into Rekordbox -> CDJ via *File > Import > Library*.

Format reference: Pioneer "xml_format_list.pdf" + pyrekordbox docs.
- TRACK: TrackID, Name, Artist, Location (file:// URL-encoded),
  TotalTime (s, int), AverageBpm (decimal), SampleRate, Kind.
- TEMPO: Inizio (sec), Bpm, Metro ("4/4"), Battito (1..4).
- POSITION_MARK: Type ("cue"/"loop"/"fade-in"/"fade-out"/"load"),
  Start, End (loops only), Num (-1 = memory cue, 0..7 = hot cue A..H).

pyrekordbox 0.4 does not expose Red/Green/Blue on marks — CDJs pick the
default color for each hot cue slot, which is the expected behavior anyway.
"""
from __future__ import annotations

from pathlib import Path
from pyrekordbox import RekordboxXml

from . import storage
from .models import Cue


def _file_uri(audio: Path) -> str:
    # pyrekordbox prepends `file://localhost` itself; we pass the absolute path.
    return str(audio.resolve())


def _mark_type(c: Cue) -> str:
    return "loop" if c.type == "loop" else "cue"


def _mark_num(c: Cue) -> int:
    if c.type == "memory":
        return -1
    if c.slot is None:
        return -1
    return int(c.slot)


def build_xml(track_ids: list[str]) -> RekordboxXml:
    xml = RekordboxXml(name="rekordbox", version="6.0.0", company="Pioneer DJ")

    for tid in track_ids:
        analysis = storage.load_analysis(tid)
        if analysis is None:
            continue
        audio = storage.audio_path(tid)
        if audio is None:
            continue

        track = xml.add_track(
            location=_file_uri(audio),
            Name=Path(analysis.filename).stem,
            Artist="",
            TotalTime=int(round(analysis.duration_sec)),
            AverageBpm=round(analysis.bpm, 2),
            SampleRate=analysis.sample_rate,
            Kind=_kind_for_suffix(audio.suffix),
        )

        # One TEMPO anchor at the first downbeat is enough for constant-tempo
        # tracks. CDJs extrapolate the grid forward from this point.
        if analysis.downbeats:
            track.add_tempo(
                Inizio=round(analysis.first_downbeat_sec, 3),
                Bpm=round(analysis.bpm, 2),
                Metro="4/4",
                Battito=1,
            )

        cues = storage.load_cues(tid)
        for c in cues.cues:
            kwargs = dict(
                Name=c.name,
                Type=_mark_type(c),
                Start=round(c.position_sec, 3),
                Num=_mark_num(c),
            )
            if c.type == "loop" and c.end_sec is not None:
                kwargs["End"] = round(c.end_sec, 3)
            track.add_mark(**kwargs)

    return xml


def export_to_file(track_ids: list[str], out_path: Path) -> Path:
    xml = build_xml(track_ids)
    xml.save(str(out_path))
    return out_path


def _kind_for_suffix(suffix: str) -> str:
    s = suffix.lower().lstrip(".")
    return {
        "mp3": "MP3 File",
        "wav": "WAV File",
        "aiff": "AIFF File",
        "aif": "AIFF File",
        "flac": "FLAC File",
        "m4a": "M4A File",
    }.get(s, "Audio File")
