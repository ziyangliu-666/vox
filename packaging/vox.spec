# PyInstaller spec for vox. Build with packaging/build_mac.sh or
# packaging/build_win.ps1, not by hand: they also make the icons.
import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent
IS_MAC = sys.platform == "darwin"
VERSION = "0.2.0"

a = Analysis(
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(ROOT)],
    datas=[
        (str(ROOT / "vox" / "ui"), "vox/ui"),
        (str(ROOT / "vox" / "bridge.js"), "vox"),
        (str(ROOT / "vox" / "icons"), "vox/icons"),
    ],
    # pynput and pystray pick their backend at import time by platform name,
    # so PyInstaller cannot see these imports.
    hiddenimports=(
        ["pynput.keyboard._darwin", "pynput.mouse._darwin", "webview.platforms.cocoa"]
        if IS_MAC else
        ["pynput.keyboard._win32", "pynput.mouse._win32", "pystray._win32",
         "webview.platforms.winforms", "webview.platforms.edgechromium"]
    ),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)

if IS_MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="vox",
              console=False, argv_emulation=False)
    coll = COLLECT(exe, a.binaries, a.datas, name="vox")
    app = BUNDLE(
        coll,
        name="vox.app",
        icon=str(ROOT / "assets" / "vox.icns"),
        bundle_identifier="app.vox.dictation",
        version=VERSION,
        info_plist={
            "CFBundleDisplayName": "vox",
            "CFBundleShortVersionString": VERSION,
            "LSUIElement": True,  # Menu bar app: no Dock icon.
            "LSMinimumSystemVersion": "12.0",
            "NSHighResolutionCapable": True,
            "NSMicrophoneUsageDescription":
                "vox records your voice while you hold the hotkey, to turn it into text.",
        },
    )
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, name="vox",
              console=False, icon=str(ROOT / "assets" / "vox.ico"))
