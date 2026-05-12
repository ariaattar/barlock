from __future__ import annotations

import json
import shutil
from pathlib import Path

from .models import Analysis, Cues

DATA_ROOT = Path(__file__).resolve().parents[2] / "data"


def track_dir(track_id: str) -> Path:
    p = DATA_ROOT / track_id
    p.mkdir(parents=True, exist_ok=True)
    return p


def audio_path(track_id: str) -> Path | None:
    d = DATA_ROOT / track_id
    if not d.exists():
        return None
    for f in d.iterdir():
        if f.name.startswith("audio"):
            return f
    return None


def save_audio(track_id: str, filename: str, content: bytes) -> Path:
    suffix = Path(filename).suffix or ".bin"
    p = track_dir(track_id) / f"audio{suffix}"
    p.write_bytes(content)
    return p


def save_analysis(analysis: Analysis) -> None:
    p = track_dir(analysis.track_id) / "analysis.json"
    p.write_text(analysis.model_dump_json(indent=2))


def load_analysis(track_id: str) -> Analysis | None:
    p = DATA_ROOT / track_id / "analysis.json"
    if not p.exists():
        return None
    return Analysis.model_validate_json(p.read_text())


def save_cues(track_id: str, cues: Cues) -> None:
    p = track_dir(track_id) / "cues.json"
    p.write_text(cues.model_dump_json(indent=2))


def load_cues(track_id: str) -> Cues:
    p = DATA_ROOT / track_id / "cues.json"
    if not p.exists():
        return Cues()
    return Cues.model_validate_json(p.read_text())


def list_tracks() -> list[Analysis]:
    if not DATA_ROOT.exists():
        return []
    out: list[Analysis] = []
    for d in sorted(DATA_ROOT.iterdir()):
        if not d.is_dir():
            continue
        a = load_analysis(d.name)
        if a:
            out.append(a)
    return out


def delete_track(track_id: str) -> bool:
    d = DATA_ROOT / track_id
    if not d.exists():
        return False
    shutil.rmtree(d)
    return True
