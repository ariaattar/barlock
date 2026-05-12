from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field


class Analysis(BaseModel):
    track_id: str
    filename: str
    duration_sec: float
    sample_rate: int
    bpm: float
    beats: list[float]
    downbeats: list[float]
    time_signature: str = "4/4"
    first_downbeat_sec: float


class Cue(BaseModel):
    slot: Optional[int] = Field(
        default=None,
        ge=0,
        le=7,
        description="Hot cue pad A-H (0-7). None means memory cue.",
    )
    name: str = ""
    position_sec: float
    color: tuple[int, int, int] = (40, 226, 20)
    type: Literal["hot", "memory", "loop"] = "hot"
    end_sec: Optional[float] = None


class Cues(BaseModel):
    cues: list[Cue] = []


class TrackSummary(BaseModel):
    track_id: str
    filename: str
    duration_sec: float
    bpm: float


class ExportRequest(BaseModel):
    track_ids: list[str]
