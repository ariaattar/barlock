from __future__ import annotations

import logging
import tempfile
import traceback
import uuid
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile

logger = logging.getLogger("mixer")
logging.basicConfig(level=logging.INFO)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from pathlib import Path as _Path

from pydantic import BaseModel

from . import analysis as analysis_mod
from . import library, rekordbox, storage
from .models import Analysis, Cues, ExportRequest, TrackSummary

app = FastAPI(title="Mixer Backend", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health():
    return {"ok": True}


@app.post("/api/analyze", response_model=Analysis)
async def analyze_endpoint(file: UploadFile = File(...)):
    if not file.filename:
        raise HTTPException(400, "filename required")
    track_id = uuid.uuid4().hex[:12]
    content = await file.read()
    audio_path = storage.save_audio(track_id, file.filename, content)
    try:
        result = analysis_mod.analyze(track_id, audio_path, original_filename=file.filename)
    except Exception as e:
        logger.error(
            "analysis failed for %s (%s): %s\n%s",
            file.filename,
            audio_path.suffix,
            e,
            traceback.format_exc(),
        )
        storage.delete_track(track_id)
        raise HTTPException(500, f"analysis failed: {e}") from e
    storage.save_analysis(result)
    return result


@app.get("/api/tracks", response_model=list[TrackSummary])
def list_tracks():
    return [
        TrackSummary(
            track_id=a.track_id,
            filename=a.filename,
            duration_sec=a.duration_sec,
            bpm=a.bpm,
        )
        for a in storage.list_tracks()
    ]


@app.get("/api/tracks/{track_id}", response_model=Analysis)
def get_track(track_id: str):
    a = storage.load_analysis(track_id)
    if not a:
        raise HTTPException(404, "not found")
    return a


@app.delete("/api/tracks/{track_id}")
def delete_track(track_id: str):
    ok = storage.delete_track(track_id)
    if not ok:
        raise HTTPException(404, "not found")
    return {"ok": True}


@app.get("/files/{track_id}")
def file_for_track(track_id: str):
    p = storage.audio_path(track_id)
    if p is None:
        raise HTTPException(404, "not found")
    return FileResponse(str(p))


@app.get("/api/tracks/{track_id}/cues", response_model=Cues)
def get_cues(track_id: str):
    if storage.load_analysis(track_id) is None:
        raise HTTPException(404, "track not found")
    return storage.load_cues(track_id)


@app.put("/api/tracks/{track_id}/cues", response_model=Cues)
def put_cues(track_id: str, cues: Cues):
    if storage.load_analysis(track_id) is None:
        raise HTTPException(404, "track not found")
    storage.save_cues(track_id, cues)
    return cues


class IngestPath(BaseModel):
    path: str


@app.post("/api/analyze_path", response_model=Analysis)
def analyze_path(req: IngestPath):
    """Analyze an audio file that already lives on this machine (no upload)."""
    src = _Path(req.path)
    if not src.exists() or not src.is_file():
        raise HTTPException(404, f"file not found: {src}")
    if src.suffix.lower() not in library.AUDIO_EXTS:
        raise HTTPException(400, f"unsupported extension: {src.suffix}")

    track_id = uuid.uuid4().hex[:12]
    audio_path = storage.save_audio(track_id, src.name, src.read_bytes())
    try:
        result = analysis_mod.analyze(
            track_id, audio_path, original_filename=src.name
        )
    except Exception as e:
        logger.error(
            "analyze_path failed for %s: %s\n%s", src, e, traceback.format_exc()
        )
        storage.delete_track(track_id)
        raise HTTPException(500, f"analysis failed: {e}") from e
    storage.save_analysis(result)
    return result


@app.get("/api/library/folders")
def library_folders():
    return {
        "roots": library.default_roots(),
        "rekordbox_available": library.rekordbox_available(),
    }


@app.get("/api/library/browse")
def library_browse(path: str):
    return library.list_folder(_Path(path))


@app.get("/api/library/rekordbox/playlists")
def library_rb_playlists():
    return {"playlists": library.rekordbox_playlists()}


@app.get("/api/library/rekordbox/playlist/{playlist_id}")
def library_rb_playlist(playlist_id: str):
    return {"songs": library.rekordbox_playlist_songs(playlist_id)}


@app.post("/api/export/rekordbox")
def export_rekordbox(req: ExportRequest):
    if not req.track_ids:
        raise HTTPException(400, "track_ids required")
    with tempfile.NamedTemporaryFile(suffix=".xml", delete=False) as tmp:
        out = Path(tmp.name)
    rekordbox.export_to_file(req.track_ids, out)
    data = out.read_bytes()
    out.unlink(missing_ok=True)
    return Response(
        content=data,
        media_type="application/xml",
        headers={"Content-Disposition": 'attachment; filename="rekordbox.xml"'},
    )
