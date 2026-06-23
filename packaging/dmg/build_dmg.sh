#!/usr/bin/env bash
set -euo pipefail

APP_PATH="${1:?Usage: build_dmg.sh <App.app> <Volume Name> <output.dmg> [assets_dir]}"
VOL_NAME="${2:?missing volume name}"
OUT_DMG="${3:?missing output dmg path}"
ASSETS_DIR="${4:-$(cd "$(dirname "${BASH_SOURCE[0]}")/assets" && pwd)}"

WIN_W=660
WIN_H=440
ICON_SIZE=128
APP_X=145
APPS_X=515
ICON_Y=220

APP_NAME="$(basename "$APP_PATH")"
WORK="$(mktemp -d)"
STAGE="$WORK/stage"
RW_DMG="$WORK/rw.dmg"
MOUNT="/Volumes/$VOL_NAME"
DEVICE=""

cleanup() {
  if [ -n "$DEVICE" ]; then
    hdiutil detach "$DEVICE" -force >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
}
trap cleanup EXIT

mkdir -p "$STAGE/.background"
cp -R "$APP_PATH" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

tiffutil -cathidpicheck \
  "$ASSETS_DIR/background-light.png" \
  "$ASSETS_DIR/background-light@2x.png" \
  -out "$STAGE/.background/background.tiff"

hdiutil create \
  -volname "$VOL_NAME" \
  -srcfolder "$STAGE" \
  -fs HFS+ \
  -format UDRW \
  -ov \
  "$RW_DMG"

DEVICE="$(hdiutil attach \
  -readwrite \
  -noverify \
  -noautoopen \
  -mountpoint "$MOUNT" \
  "$RW_DMG" \
  | awk '/^\/dev\// { print $1; exit }')"
test -n "$DEVICE"
test -d "$MOUNT/$APP_NAME"

sleep 2

osascript <<APPLESCRIPT
tell application "Finder"
  tell disk "$VOL_NAME"
    open
    set current view of container window to icon view
    set toolbar visible of container window to false
    set statusbar visible of container window to false
    set the bounds of container window to {200, 140, 200 + $WIN_W, 140 + $WIN_H}
    set theViewOptions to the icon view options of container window
    set arrangement of theViewOptions to not arranged
    set icon size of theViewOptions to $ICON_SIZE
    set text size of theViewOptions to 13
    set background picture of theViewOptions to file ".background:background.tiff"
    set position of item "$APP_NAME" of container window to {$APP_X, $ICON_Y}
    set position of item "Applications" of container window to {$APPS_X, $ICON_Y}
    close
    open
    update
    delay 2
  end tell
end tell
APPLESCRIPT

SetFile -a C "$MOUNT" 2>/dev/null || true
sync

hdiutil detach "$DEVICE" -force >/dev/null 2>&1 \
  || { sleep 3; hdiutil detach "$DEVICE" -force; }
DEVICE=""

rm -f "$OUT_DMG"
hdiutil convert "$RW_DMG" -format UDZO -imagekey zlib-level=9 -ov -o "$OUT_DMG"

echo "Created $OUT_DMG"
