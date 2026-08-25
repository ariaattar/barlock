#!/usr/bin/env bash
set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR="$BACKEND_DIR/desktop"
export UV_BIN="${UV_BIN:-$(command -v uv)}"

# Avoid unrelated ancestor node_modules directories changing which npm Tauri
# uses for its package-version check.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.bun/bin:$HOME/.cargo/bin:$HOME/.local/bin"
cd "$DESKTOP_DIR"
./node_modules/.bin/tauri build --ci --ignore-version-mismatches --bundles app "$@"

APP_PATH="$DESKTOP_DIR/src-tauri/target/release/bundle/macos/SoundCloud DL.app"
DMG_DIR="$DESKTOP_DIR/src-tauri/target/release/bundle/dmg"
DMG_PATH="$DMG_DIR/SoundCloud DL_0.1.0_aarch64.dmg"

if [[ -z "${APPLE_SIGNING_IDENTITY:-}" ]]; then
  codesign --force --deep --sign - "$APP_PATH"
fi
codesign --verify --deep --strict "$APP_PATH"

STAGE_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE_DIR"' EXIT
cp -R "$APP_PATH" "$STAGE_DIR/"
ln -s /Applications "$STAGE_DIR/Applications"
mkdir -p "$DMG_DIR"
hdiutil create \
  -volname "SoundCloud DL" \
  -srcfolder "$STAGE_DIR" \
  -ov \
  -format UDZO \
  "$DMG_PATH"

printf 'Desktop app: %s\n' "$APP_PATH"
printf 'Installer: %s\n' "$DMG_PATH"
