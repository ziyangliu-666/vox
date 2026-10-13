"""The flow bar on macOS: docked to a screen edge, draggable, with guides.

The bar window covers the whole screen and lets every click through. The
page (hud.html, "free mode") draws the pill at its dock place, runs the
drag, shows the alignment guides, and animates the snap. This module:

- sizes the window to the screen and tells the page which part of it is
  usable right now (not under the Dock or the menu bar);
- watches the pointer, and lets the window take the mouse only while the
  pointer is over the pill, so the rest of the screen stays clickable;
- saves the place the page reports, and after a drag gives focus back to
  the app the user was typing in.

Positions are AppKit screen points (origin at the bottom left), except the
page's, which are CSS px from the top left of the window.
"""

import json
import time

import AppKit
import objc
import Quartz
from PyObjCTools import AppHelper
from webview.platforms.cocoa import BrowserView

# PyObjC requires a category class to carry the name of the class it extends.
class WebKitHost(objc.Category(BrowserView.WebKitHost)):
    """Pass the first click to the page.

    The bar window never becomes the key window, so to AppKit every click
    on it is a "first mouse" click, and WKWebView by default only uses that
    click to activate the window: the page never saw a press, and the pill
    could not be clicked or dragged. Other vox windows lose nothing: a
    click into them now also acts at once.
    """

    def acceptsFirstMouse_(self, event):
        return True


# The Dock's full-screen backdrop; the wallpaper sits two levels lower.
BACKDROP_LEVEL = -2147483622
GRAB = 28  # Minimum hit size, so the thin idle pill is easy to grab.


def usable(screen):
    """The part of the screen the bar may touch, as it is right now.

    In a normal Space that is NSScreen.visibleFrame: it leaves out the menu
    bar and the Dock, and follows Dock auto-hide. In a full-screen Space
    both are hidden, but visibleFrame still leaves room for them, and the
    bar would float 60 pt above the real edge; there the whole screen is
    usable. The Dock draws its backdrop window only then.

    The Dock strip's own window cannot measure the Dock: on macOS 26 it
    covers the whole screen, and reading it as a strip made the usable
    area collapse and the bar jump.
    """
    on_screen = Quartz.CGWindowListCopyWindowInfo(
        Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    # Match the backdrop by its level, not its title: macOS gives window
    # titles only to apps with Screen Recording access, so vox never saw
    # "Fullscreen Backdrop" and kept room for a hidden Dock.
    fullscreen = any(w.get("kCGWindowOwnerName") == "Dock"
                     and w.get("kCGWindowLayer") == BACKDROP_LEVEL for w in on_screen)
    return screen.frame() if fullscreen else screen.visibleFrame()


class FlowBar:
    def __init__(self, window, saved, on_save, on_click) -> None:
        """window: the pywebview HUD window; saved: {"edge", "t"} or None."""
        self.window = window
        self.place = saved if saved and saved.get("edge") else None
        self._on_save, self._on_click = on_save, on_click
        self._pill = None  # (x, y, w, h) in window CSS px.
        self._hover = False
        self._last_app = None  # The app to give focus back to after a drag.
        self._insets = None
        self.grabbable = True

    # ---- calls from the page (through HudApi) ------------------------------

    def pill_rect(self, rect) -> None:
        self._pill = rect

    def save_place(self, place) -> None:
        self.place = place
        self._on_save(place)

    def drag_end(self) -> None:
        # The press on the bar made vox the active app; the user was typing
        # somewhere else and expects to keep typing there.
        if self._last_app is not None:
            self._last_app.activateWithOptions_(0)

    def clicked(self) -> None:
        self._on_click()

    @property
    def hovered(self) -> bool:
        return self._hover

    # ---- placement ---------------------------------------------------------

    def _screen(self):
        return AppKit.NSScreen.screens()[0]

    def _send_area(self) -> None:
        f = self._screen().frame()
        u = usable(self._screen())
        insets = {
            "left": u.origin.x - f.origin.x,
            "right": f.origin.x + f.size.width - (u.origin.x + u.size.width),
            "top": f.origin.y + f.size.height - (u.origin.y + u.size.height),
            "bottom": u.origin.y - f.origin.y,
        }
        # macOS moves visibleFrame by a point or two now and then; following
        # that would twitch the bar. Only a real change (Dock, menu bar,
        # full screen) moves it.
        if self._insets is None or any(abs(insets[k] - self._insets[k]) >= 4 for k in insets):
            print(f"[vox] flow bar area {insets}", flush=True)
            self._insets = insets
            self.window.run_js(f"window.hud && window.hud.area({json.dumps(insets)})")

    def start(self) -> None:
        """Cover the screen, place the pill, and send the page what it needs."""
        AppHelper.callAfter(self.window.native.setFrame_display_, self._screen().frame(), True)
        time.sleep(0.2)  # Let the page see its new size before it lays out.
        self.sync_page()

    def sync_page(self) -> None:
        """Send the area and the saved place; also after the page reloads."""
        self._insets = None
        self._send_area()
        self.window.run_js(f"window.hud && window.hud.restore({json.dumps(self.place)})")

    def reset(self) -> None:
        self.save_place(None)
        self.window.run_js("window.hud && window.hud.restore(null)")

    # ---- pointer -----------------------------------------------------------

    def _hit(self, loc) -> bool:
        if not self._pill or not self.grabbable:
            return False
        frame = self.window.native.frame()
        px, py, pw, ph = self._pill
        cx = frame.origin.x + px + pw / 2
        cy = frame.origin.y + frame.size.height - (py + ph / 2)
        return abs(loc.x - cx) <= max(pw, GRAB) / 2 and abs(loc.y - cy) <= max(ph, GRAB) / 2

    def _set_hover(self, on: bool) -> None:
        if on == self._hover:
            return
        self._hover = on
        AppHelper.callAfter(self.window.native.setIgnoresMouseEvents_, not on)
        self.window.run_js(f"window.hud && window.hud.hover({'true' if on else 'false'})")

    def _remember_front_app(self) -> None:
        app = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        me = AppKit.NSRunningApplication.currentApplication().processIdentifier()
        if app and app.processIdentifier() != me:
            self._last_app = app

    def run(self) -> None:
        """Poll the pointer forever; start in a daemon thread."""
        tick = 0
        while True:
            time.sleep(1 / 60)
            tick += 1
            down = AppKit.NSEvent.pressedMouseButtons() & 1
            if self._hover and down:
                continue  # A drag is on: the page owns the pointer until release.
            if tick % 60 == 0:
                # A full-screen app or the Dock came or went: follow the edge.
                self._send_area()
            if not self._hover:
                self._remember_front_app()
            hit = self._hit(AppKit.NSEvent.mouseLocation())
            # A press that starts elsewhere and slides over the bar is not a grab.
            self._set_hover(hit and (self._hover or not down))
