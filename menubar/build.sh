#!/bin/bash
# Builds Hamzaban.app (menu-bar switch) into ~/Applications.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HOME/Applications}"
BUILD="$(mktemp -d)"
APPDIR="$BUILD/Hamzaban.app/Contents"
mkdir -p "$APPDIR/MacOS" "$APPDIR/Resources"

swiftc -O -swift-version 5 -parse-as-library -target arm64-apple-macosx14.0 \
  -o "$APPDIR/MacOS/Hamzaban" "$HERE/HamzabanBar.swift"

# app icon from the shared SVG
ICON="$BUILD/icon.iconset"
mkdir -p "$ICON"
sed 's/width="128" height="128"/width="1024" height="1024"/' "$HERE/../chrome/icons/icon.svg" > "$BUILD/icon.svg"
qlmanage -t -s 1024 -o "$BUILD" "$BUILD/icon.svg" >/dev/null 2>&1
for s in 16 32 128 256 512; do
  sips -z $s $s "$BUILD/icon.svg.png" --out "$ICON/icon_${s}x${s}.png" >/dev/null
  sips -z $((s * 2)) $((s * 2)) "$BUILD/icon.svg.png" --out "$ICON/icon_${s}x${s}@2x.png" >/dev/null
done
iconutil -c icns "$ICON" -o "$APPDIR/Resources/AppIcon.icns"

cat > "$APPDIR/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleIdentifier</key><string>com.hamzaban.menubar</string>
  <key>CFBundleName</key><string>Hamzaban</string>
  <key>CFBundleDisplayName</key><string>هم‌زبان</string>
  <key>CFBundleExecutable</key><string>Hamzaban</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>14.0</string>
  <key>LSUIElement</key><true/>
  <key>NSAppTransportSecurity</key>
  <dict><key>NSAllowsLocalNetworking</key><true/></dict>
</dict>
</plist>
PLIST

codesign --force --sign - "$BUILD/Hamzaban.app" >/dev/null 2>&1 || true
mkdir -p "$OUT"
pkill -x Hamzaban 2>/dev/null || true
rm -rf "$OUT/Hamzaban.app"
cp -R "$BUILD/Hamzaban.app" "$OUT/Hamzaban.app"
rm -rf "$BUILD"
echo "$OUT/Hamzaban.app"
