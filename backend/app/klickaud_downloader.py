"""KlickAud client with SoundCloud API URL resolution through yt-dlp."""
from __future__ import annotations

import http.cookiejar
import json
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import HTTPCookieProcessor, Request, build_opener

BASE_URL = "https://www.klickaud.org"
LANDING_URL = f"{BASE_URL}/en17/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 Chrome/140.0 Safari/537.36"
)

StatusCallback = Callable[[str], None]


class KlickAudDownloadError(RuntimeError):
    """Raised when KlickAud cannot prepare or return an MP3."""


def _request(opener, url: str, *, data=None, headers=None, timeout: int = 180):
    request_headers = {"User-Agent": USER_AGENT}
    if headers:
        request_headers.update(headers)
    return opener.open(Request(url, data=data, headers=request_headers), timeout=timeout)


def _read_text(response) -> str:
    charset = response.headers.get_content_charset() or "utf-8"
    return response.read().decode(charset, errors="replace")


def _extract_js_string(page: str, variable: str) -> str:
    pattern = rf"const\s+{re.escape(variable)}\s*=\s*(\"(?:\\.|[^\"\\])*\")\s*;"
    match = re.search(pattern, page)
    if not match:
        raise KlickAudDownloadError(f"KlickAud response did not contain {variable!r}")
    return str(json.loads(match.group(1)))


def _public_track_url(url: str, *, timeout: int) -> str:
    """KlickAud's worker requires a public permalink, not a SoundCloud API URL."""
    if urlparse(url).hostname not in {"api.soundcloud.com", "api-v2.soundcloud.com"}:
        return url

    from yt_dlp import YoutubeDL

    # Metadata is still available when yt-dlp cannot download the audio formats.
    with YoutubeDL({
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "ignore_no_formats_error": True,
        "noplaylist": True,
        "socket_timeout": timeout,
    }) as ydl:
        info = ydl.extract_info(url, download=False)
    permalink = (info or {}).get("webpage_url")
    parsed = urlparse(permalink if isinstance(permalink, str) else "")
    if parsed.scheme not in {"http", "https"} or parsed.hostname not in {
        "soundcloud.com", "www.soundcloud.com", "m.soundcloud.com",
    } or len(parsed.path.strip("/").split("/")) < 2:
        raise KlickAudDownloadError("Could not resolve the SoundCloud API URL to a public track link")
    return permalink


def _worker_download_url(
    opener,
    soundcloud_url: str,
    grant: str,
    *,
    timeout: int,
    status: StatusCallback | None,
) -> str:
    capability_body = json.dumps(
        {"grant": grant, "url": soundcloud_url}, separators=(",", ":")
    ).encode()
    with _request(
        opener,
        f"{BASE_URL}/sse_capability.php",
        data=capability_body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "Referer": f"{BASE_URL}/download.php",
        },
        timeout=timeout,
    ) as response:
        capability_data = json.loads(_read_text(response))

    capability = capability_data.get("capability")
    if not capability:
        raise KlickAudDownloadError("KlickAud did not issue a worker capability")

    query = urlencode({"url": soundcloud_url, "cap": capability})
    current_event = "message"
    data_lines: list[str] = []
    with _request(
        opener,
        f"{BASE_URL}/worker_sse.php?{query}",
        headers={
            "Accept": "text/event-stream",
            "Cache-Control": "no-cache",
            "Referer": f"{BASE_URL}/download.php",
        },
        timeout=timeout,
    ) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line.startswith("event:"):
                current_event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif not line:
                payload: dict = {}
                if data_lines:
                    try:
                        payload = json.loads("\n".join(data_lines))
                    except json.JSONDecodeError:
                        pass

                if current_event == "progress" and status and payload.get("message"):
                    status(str(payload["message"]))
                elif current_event == "ready" and payload.get("download_url"):
                    return str(payload["download_url"])
                elif current_event == "failed":
                    message = payload.get("message", "KlickAud worker failed")
                    raise KlickAudDownloadError(str(message))

                current_event = "message"
                data_lines = []

    raise KlickAudDownloadError("KlickAud's worker ended without an audio URL")


def _save_audio(opener, audio_url: str, destination: Path, *, timeout: int) -> Path:
    destination = destination.expanduser().resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None

    try:
        with _request(
            opener,
            audio_url,
            headers={
                "Accept": "audio/mpeg,*/*;q=0.8",
                "Referer": f"{BASE_URL}/download.php",
            },
            timeout=timeout,
        ) as response:
            content_type = response.headers.get_content_type().lower()
            first_chunk = response.read(64 * 1024)
            looks_like_mp3 = first_chunk.startswith(b"ID3") or (
                len(first_chunk) >= 2
                and first_chunk[0] == 0xFF
                and first_chunk[1] & 0xE0 == 0xE0
            )
            allowed_type = content_type.startswith("audio/") or content_type in {
                "application/octet-stream",
                "binary/octet-stream",
            }
            if not first_chunk:
                raise KlickAudDownloadError("KlickAud returned an empty audio response")
            if not (looks_like_mp3 or allowed_type):
                raise KlickAudDownloadError(
                    f"KlickAud returned {content_type!r} instead of MP3 audio"
                )

            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix=f".{destination.name}.",
                suffix=".part",
                dir=destination.parent,
                delete=False,
            ) as temporary:
                temporary_path = Path(temporary.name)
                temporary.write(first_chunk)
                shutil.copyfileobj(response, temporary, length=1024 * 1024)

        temporary_path.replace(destination)
        return destination
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def download_mp3(
    soundcloud_url: str,
    destination: Path,
    *,
    timeout: int = 180,
    status: StatusCallback | None = None,
) -> Path:
    """Download one SoundCloud track through KlickAud and return its MP3 path."""
    parsed = urlparse(soundcloud_url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"http", "https"} or not (
        host == "soundcloud.com" or host.endswith(".soundcloud.com")
    ):
        raise ValueError(f"expected a SoundCloud URL, got: {soundcloud_url}")

    soundcloud_url = _public_track_url(soundcloud_url, timeout=timeout)
    opener = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    with _request(opener, LANDING_URL, timeout=timeout) as response:
        response.read()

    with _request(
        opener,
        f"{BASE_URL}/csrf-token-endpoint.php",
        headers={"Accept": "application/json", "Referer": LANDING_URL},
        timeout=timeout,
    ) as response:
        csrf_data = json.loads(_read_text(response))
    csrf_token = csrf_data.get("csrf_token")
    if not csrf_token:
        raise KlickAudDownloadError("KlickAud did not return a CSRF token")

    form_body = urlencode(
        {"value": soundcloud_url, "csrf_token": csrf_token}
    ).encode()
    with _request(
        opener,
        f"{BASE_URL}/download.php",
        data=form_body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "Referer": LANDING_URL,
        },
        timeout=timeout,
    ) as response:
        download_page = _read_text(response)

    mode = _extract_js_string(download_page, "downloadMode")
    if mode == "direct":
        audio_url = _extract_js_string(download_page, "directDownloadUrl")
        if not audio_url:
            raise KlickAudDownloadError("KlickAud returned an empty direct download URL")
    elif mode == "worker":
        grant = _extract_js_string(download_page, "sseGrant")
        audio_url = _worker_download_url(
            opener,
            soundcloud_url,
            grant,
            timeout=timeout,
            status=status,
        )
    else:
        raise KlickAudDownloadError(f"Unsupported KlickAud download mode: {mode!r}")

    return _save_audio(opener, audio_url, destination, timeout=timeout)
