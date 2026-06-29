from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict, fields
from pathlib import Path
from typing import Any

from .audio_features import CueHint, Section, TrackFeatures, VocalInterval


def resolve_download_path(value: str, default: Path) -> Path:
    if not value:
        return default.expanduser()
    path = Path(value).expanduser()
    if path.is_absolute():
        return path
    if len(path.parts) == 1:
        return Path.home() / "Downloads" / path
    return (Path.home() / "Downloads" / path).expanduser()


def archive_for_output(output_dir: Path, source_name: str | None = None) -> Path:
    output_dir = output_dir.expanduser()
    if not source_name:
        return output_dir / ".soundcloud-archive.txt"

    archive_dir = output_dir / ".soundcloud-archives"
    archive_path = archive_dir / f"{safe_slug(source_name)}.txt"
    legacy_path = output_dir / ".soundcloud-archive.txt"
    if not archive_path.exists() and legacy_path.exists():
        archive_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy_path, archive_path)
    return archive_path


def safe_slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", value.strip()).strip("-._").lower()
    return slug[:80] or "soundcloud"


def features_to_dict(features: TrackFeatures) -> dict[str, Any]:
    data = asdict(features)
    data["cue_hints"] = [asdict(cue) for cue in features.cue_hints]
    data["sections"] = [asdict(s) for s in features.sections]
    data["vocals"] = [asdict(v) for v in features.vocals]
    return data


def features_from_dict(data: dict[str, Any]) -> TrackFeatures:
    copied = dict(data)
    cue_hints = []
    for cue in copied.get("cue_hints", []):
        cue_data = dict(cue)
        if "loop_bars" in cue_data and "loop_beats" not in cue_data:
            cue_data["loop_beats"] = int(cue_data.pop("loop_bars") or 0) * 4 or None
        else:
            cue_data.pop("loop_bars", None)
        cue_hints.append(CueHint(**cue_data))
    copied["cue_hints"] = cue_hints
    copied["analysis_version"] = int(copied.get("analysis_version", 0) or 0)
    copied["sections"] = [Section(**s) for s in copied.get("sections", [])]
    copied["vocals"] = [VocalInterval(**v) for v in copied.get("vocals", [])]
    valid = {f.name for f in fields(TrackFeatures)}
    copied = {k: v for k, v in copied.items() if k in valid}
    return TrackFeatures(**copied)


def features_from_json(text: str) -> list[TrackFeatures]:
    raw = json.loads(text)
    if not isinstance(raw, list):
        raise ValueError("expected a list of track features")
    return [features_from_dict(item) for item in raw]
