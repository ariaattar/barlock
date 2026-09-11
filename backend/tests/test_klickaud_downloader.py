from __future__ import annotations

from email.message import Message
from io import BytesIO
import json
from urllib.parse import parse_qs

import pytest
import yt_dlp

from app import klickaud_downloader as klickaud


class FakeResponse(BytesIO):
    def __init__(self, body: bytes, content_type: str):
        super().__init__(body)
        self.headers = Message()
        self.headers["Content-Type"] = content_type

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return next(self.responses)


@pytest.mark.parametrize("source_url", [
    "https://soundcloud.com/user/track",
    "https://api-v2.soundcloud.com/tracks/123",
    "https://api.soundcloud.com/tracks/123",
])
def test_download_mp3_follows_worker_flow(monkeypatch, tmp_path, source_url):
    class MetadataYDL:
        def __init__(self, options):
            assert options["ignore_no_formats_error"] is True

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def extract_info(self, url, download):
            assert "api" in source_url
            assert url == source_url
            assert download is False
            return {"webpage_url": "https://soundcloud.com/user/track"}

    monkeypatch.setattr(yt_dlp, "YoutubeDL", MetadataYDL)
    download_page = b"""
        <script>
        const downloadMode = "worker";
        const sseGrant = "grant-token";
        </script>
    """
    events = b"""
event: progress
data: {"message":"Preparing audio","percent":20}

event: ready
data: {"download_url":"https://www.klickaud.org/audio/result.mp3"}

"""
    opener = FakeOpener(
        [
            FakeResponse(b"landing", "text/html"),
            FakeResponse(b'{"csrf_token":"csrf-token"}', "application/json"),
            FakeResponse(download_page, "text/html"),
            FakeResponse(b'{"capability":"worker-capability"}', "application/json"),
            FakeResponse(events, "text/event-stream"),
            FakeResponse(b"ID3\x00\x00\x00fake mp3", "audio/mpeg"),
        ]
    )
    monkeypatch.setattr(klickaud, "build_opener", lambda *handlers: opener)
    statuses: list[str] = []
    destination = tmp_path / "Track [123].mp3"

    result = klickaud.download_mp3(
        source_url,
        destination,
        status=statuses.append,
    )

    assert result == destination.resolve()
    assert destination.read_bytes() == b"ID3\x00\x00\x00fake mp3"
    assert statuses == ["Preparing audio"]
    assert parse_qs(opener.requests[2].data.decode())["value"] == ["https://soundcloud.com/user/track"]
    assert json.loads(opener.requests[3].data)["url"] == "https://soundcloud.com/user/track"
    assert [request.full_url for request in opener.requests] == [
        "https://www.klickaud.org/en17/",
        "https://www.klickaud.org/csrf-token-endpoint.php",
        "https://www.klickaud.org/download.php",
        "https://www.klickaud.org/sse_capability.php",
        (
            "https://www.klickaud.org/worker_sse.php?"
            "url=https%3A%2F%2Fsoundcloud.com%2Fuser%2Ftrack&cap=worker-capability"
        ),
        "https://www.klickaud.org/audio/result.mp3",
    ]


def test_download_mp3_supports_direct_flow(monkeypatch, tmp_path):
    download_page = b"""
        <script>
        const downloadMode = "direct";
        const directDownloadUrl = "https://cdn.example/audio.mp3";
        </script>
    """
    opener = FakeOpener(
        [
            FakeResponse(b"landing", "text/html"),
            FakeResponse(b'{"csrf_token":"csrf-token"}', "application/json"),
            FakeResponse(download_page, "text/html"),
            FakeResponse(b"\xff\xfb\x90\x64fake mp3", "application/octet-stream"),
        ]
    )
    monkeypatch.setattr(klickaud, "build_opener", lambda *handlers: opener)
    destination = tmp_path / "direct.mp3"

    result = klickaud.download_mp3(
        "https://soundcloud.com/user/direct",
        destination,
    )

    assert result == destination.resolve()
    assert destination.read_bytes() == b"\xff\xfb\x90\x64fake mp3"
