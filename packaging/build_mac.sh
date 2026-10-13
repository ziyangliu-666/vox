#!/bin/sh
# Build dist/vox.app and dist/vox.dmg on macOS.
set -eu
cd "$(dirname "$0")/.."

uv sync
uv run python packaging/make_icons.py

iconutil -c icns build/vox.iconset -o assets/vox.icns

uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/vox.spec

# macOS 26 puts a plain .icns on a grey plate; from Assets.car it draws the
# icon on its own plate. Compiling a .icon needs actool from Xcode 26. The
# release build sets VOX_REQUIRE_ICON=1, so it cannot quietly ship without.
# actool reports errors on stdout, as a plist; show it when it fails.
if xcrun actool build/AppIcon.icon --compile dist/vox.app/Contents/Resources \
    --platform macosx --minimum-deployment-target 12.0 --target-device mac \
    --app-icon AppIcon --output-partial-info-plist build/AppIcon.plist >build/actool.log 2>&1 \
    && [ -f dist/vox.app/Contents/Resources/Assets.car ]; then
  echo "Compiled Assets.car"
elif [ "${VOX_REQUIRE_ICON:-0}" = 1 ]; then
  cat build/actool.log >&2
  echo "actool could not compile build/AppIcon.icon (needs Xcode 26)" >&2
  exit 1
else
  echo "warning: no Assets.car (needs Xcode 26); macOS 26 will show the icon on a grey plate" >&2
fi

# macOS ties privacy permissions (microphone, Accessibility, Input
# Monitoring) to the code signature. An ad-hoc signature changes with each
# build, so every update asked for them again. Signed with the same
# certificate, each build has the same designated requirement and keeps
# them. VOX_SIGN_IDENTITY is that certificate's SHA-1 in a keychain on the
# search list; release builds set VOX_REQUIRE_SIGN=1 so they cannot fall
# back to ad hoc. Local builds without it are signed ad hoc.
identity="${VOX_SIGN_IDENTITY:--}"
if [ "$identity" = "-" ] && [ "${VOX_REQUIRE_SIGN:-0}" = 1 ]; then
  echo "no signing identity (VOX_SIGN_IDENTITY)" >&2
  exit 1
fi
codesign --force --deep --sign "$identity" dist/vox.app
codesign -dr - dist/vox.app 2>&1 | tail -1

# The disk image holds the app and a link to /Applications, so the window
# shows where to drag it.
rm -rf dist/dmg dist/vox.dmg
mkdir dist/dmg
ditto dist/vox.app dist/dmg/vox.app
ln -s /Applications dist/dmg/Applications
hdiutil create -volname vox -srcfolder dist/dmg -ov -format UDZO dist/vox.dmg >/dev/null
rm -rf dist/dmg
echo "Built dist/vox.app and dist/vox.dmg"
