"""Menu bar icon (macOS) or tray icon (Windows) behind one small API.

Tray(items, on_click) shows the icon. set_state() switches the icon for
idle, listening, busy, and error. items is a list of (title, callback);
a title of None draws a separator.
"""

import sys
import threading
from pathlib import Path

ICONS = Path(__file__).with_name("icons")

IS_MAC = sys.platform == "darwin"

if IS_MAC:
    import AppKit
    import objc
    from PyObjCTools import AppHelper

    # The vox caret for idle and listening; SF Symbols for busy and error,
    # which must look different at a glance. All are template images, so
    # they follow the menu bar colour.
    _FILES = {"idle": "menubar.png", "listening": "menubar-listening.png"}
    _SYMBOLS = {"idle": "waveform", "listening": "waveform.circle.fill",
                "busy": "ellipsis.circle", "error": "exclamationmark.triangle"}

    def _image(state: str):
        path = ICONS / _FILES.get(state, "")
        if state in _FILES and path.exists():
            img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(path))
            img.setSize_((18, 18))  # The file is drawn at 2x.
        else:
            img = AppKit.NSImage.imageWithSystemSymbolName_accessibilityDescription_(
                _SYMBOLS[state], "vox")
        img.setTemplate_(True)
        return img

    class _Target(AppKit.NSObject):
        def initWithCallbacks_(self, callbacks):
            self = objc.super(_Target, self).init()
            self.callbacks = callbacks
            return self

        def fire_(self, sender):
            cb = self.callbacks[sender.tag()]
            # Leave the AppKit thread at once; callbacks may block on pywebview.
            threading.Thread(target=cb, daemon=True).start()

    class Tray:
        def __init__(self, items) -> None:
            self._items = items
            self._item = None
            AppHelper.callAfter(self._build)

        def _build(self) -> None:
            bar = AppKit.NSStatusBar.systemStatusBar()
            self._item = bar.statusItemWithLength_(AppKit.NSVariableStatusItemLength)
            self._target = _Target.alloc().initWithCallbacks_([cb for _, cb in self._items])
            menu = AppKit.NSMenu.alloc().init()
            for tag, (title, _) in enumerate(self._items):
                if title is None:
                    menu.addItem_(AppKit.NSMenuItem.separatorItem())
                    continue
                mi = AppKit.NSMenuItem.alloc().initWithTitle_action_keyEquivalent_(
                    title, "fire:", "")
                mi.setTag_(tag)
                mi.setTarget_(self._target)
                menu.addItem_(mi)
            self._item.setMenu_(menu)
            self._apply("idle")

        def _apply(self, state: str) -> None:
            self._item.button().setImage_(_image(state))

        def set_state(self, state: str) -> None:
            if self._item is not None:
                AppHelper.callAfter(self._apply, state)

else:
    import pystray
    from PIL import Image

    def _image(state: str):
        return Image.open(ICONS / f"tray-{state}.png")

    class Tray:
        def __init__(self, items) -> None:
            entries = [pystray.Menu.SEPARATOR if t is None
                       else pystray.MenuItem(t, (lambda cb: lambda *_: cb())(cb),
                                             default=(i == 0))
                       for i, (t, cb) in enumerate(items)]
            self._icon = pystray.Icon("vox", _image("idle"), "vox", pystray.Menu(*entries))
            self._icon.run_detached()

        def set_state(self, state: str) -> None:
            self._icon.icon = _image(state)
