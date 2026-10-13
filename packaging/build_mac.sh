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

rm -f dist/vox.dmg
hdiutil create -volname vox -srcfolder dist/vox.app -ov -format UDZO dist/vox.dmg >/dev/null
echo "Built dist/vox.app and dist/vox.dmg"
