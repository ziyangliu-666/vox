#!/bin/sh
# Build dist/vox.app and dist/vox.dmg on macOS.
set -eu
cd "$(dirname "$0")/.."

uv sync
uv run python packaging/make_icons.py

iconutil -c icns build/vox.iconset -o assets/vox.icns

uv run pyinstaller --noconfirm --clean --distpath dist --workpath build packaging/vox.spec

# Ad-hoc signature. macOS ties privacy permissions to the signature, and an
# unsigned bundle loses them on each rebuild.
codesign --force --deep --sign - dist/vox.app

# The disk image holds the app and a link to /Applications, so the window
# shows where to drag it.
rm -rf dist/dmg dist/vox.dmg
mkdir dist/dmg
ditto dist/vox.app dist/dmg/vox.app
ln -s /Applications dist/dmg/Applications
hdiutil create -volname vox -srcfolder dist/dmg -ov -format UDZO dist/vox.dmg >/dev/null
rm -rf dist/dmg
echo "Built dist/vox.app and dist/vox.dmg"
