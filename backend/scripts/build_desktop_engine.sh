#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="$BACKEND_DIR/desktop"
RESOURCE_DIR="$DESKTOP_DIR/src-tauri/resources/engine"
BUILD_DIR="$DESKTOP_DIR/.pyinstaller"
UV_BIN="${UV_BIN:-$(command -v uv)}"

cd "$BACKEND_DIR"
"$UV_BIN" sync
"$UV_BIN" run pyinstaller \
  --noconfirm \
  --clean \
  --onedir \
  --name soundcloud-worker \
  --paths "$BACKEND_DIR" \
  --distpath "$RESOURCE_DIR" \
  --workpath "$BUILD_DIR/build" \
  --specpath "$BUILD_DIR" \
  --collect-all imageio_ffmpeg \
  --collect-all pyrekordbox \
  --copy-metadata yt-dlp \
  --exclude-module IPython \
  --exclude-module jupyter \
  --exclude-module matplotlib \
  "$DESKTOP_DIR/engine/worker.py"

WORKER="$RESOURCE_DIR/soundcloud-worker/soundcloud-worker"
test -x "$WORKER"
"$WORKER" config >/dev/null
printf 'Desktop engine ready: %s\n' "$WORKER"
