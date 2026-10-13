"""Drag the flow bar and dock it to a screen edge (Windows).

On Windows the bar is a small window, not a full-screen layer: a
click-through full-screen window does not mix well with WebView2. So the
window itself moves. A press on the pill calls drag(); it follows the
cursor until the left button is up, then docks to the nearest edge of the
work area (the screen without the taskbar), on a magnet at 1/3, 1/2 or
2/3 when it is close to one, and glides there.

All sizes are WinForms device pixels; the CSS sizes from hud.html are
scaled by the window's DPI.
"""

import ctypes
import time

PAD, GAP = 14, 4  # hud.html: pill to window edge; pill to screen edge.
FLAT = (360, 72)  # Window size on the bottom and top edges, CSS px.
UPRIGHT = (360, 140)  # On the left and right edges.
MAGNETS = (1 / 3, 1 / 2, 2 / 3)
PULL = 48  # CSS px
CLICK = 4  # Cursor travel below this, in device px, is a click.


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


def _cursor() -> tuple[int, int]:
    p = _Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _left_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


class WinBar:
    def __init__(self, window, saved, on_save, on_click) -> None:
        self.window = window
        self.place = saved if saved and saved.get("edge") else {"edge": "bottom", "t": 0.5}
        self._on_save, self._on_click = on_save, on_click

    # ---- WinForms helpers --------------------------------------------------

    def _ui(self, fn):
        from System import Action

        form = self.window.native
        form.Invoke(Action(lambda: fn(form)))

    def _scale(self) -> float:
        return self.window.native.DeviceDpi / 96

    def _work_area(self, point=None):
        from System.Drawing import Point
        from System.Windows.Forms import Screen

        if point is None:
            return Screen.FromControl(self.window.native).WorkingArea
        return Screen.FromPoint(Point(*point)).WorkingArea

    def _bounds(self, wa, edge: str, t: float):
        s = self._scale()
        w, h = (v * s for v in (FLAT if edge in ("bottom", "top") else UPRIGHT))
        pad, gap = PAD * s, GAP * s
        if edge == "bottom":
            x, y = wa.Left + t * wa.Width - w / 2, wa.Bottom - h + pad - gap
        elif edge == "top":
            x, y = wa.Left + t * wa.Width - w / 2, wa.Top + gap - pad
        elif edge == "left":
            x, y = wa.Left + gap - pad, wa.Top + t * wa.Height - h / 2
        else:
            x, y = wa.Right - w + pad - gap, wa.Top + t * wa.Height - h / 2
        return int(x), int(y), int(w), int(h)

    def _set_bounds(self, x, y, w, h) -> None:
        def fn(form):
            from System.Drawing import Rectangle

            form.Bounds = Rectangle(x, y, w, h)
        self._ui(fn)

    # ---- placement ---------------------------------------------------------

    def restore(self) -> None:
        edge = self.place["edge"]
        self.window.run_js(f"window.hud && window.hud.dock('{edge}')")
        self._set_bounds(*self._bounds(self._work_area(), edge, self.place.get("t", 0.5)))

    def reset(self) -> None:
        self.place = {"edge": "bottom", "t": 0.5}
        self._on_save(None)
        self.restore()

    # ---- drag --------------------------------------------------------------

    def drag(self) -> None:
        """Follow the cursor while the left button is down; then dock or click."""
        sx, sy = _cursor()
        form = self.window.native
        ox, oy, w, h = form.Left, form.Top, form.Width, form.Height
        moved = False
        while _left_down():
            cx, cy = _cursor()
            if abs(cx - sx) + abs(cy - sy) >= CLICK:
                moved = True
            if moved:
                self._set_bounds(ox + cx - sx, oy + cy - sy, w, h)
            time.sleep(0.008)
        if not moved:
            self._on_click()
            return
        self._dock(_cursor())

    def _dock(self, cursor) -> None:
        wa = self._work_area(cursor)
        fx = min(max((cursor[0] - wa.Left) / wa.Width, 0), 1)
        fy = min(max((cursor[1] - wa.Top) / wa.Height, 0), 1)  # 0 at the top.
        dist = {"bottom": (1 - fy) * wa.Height, "top": fy * wa.Height,
                "left": fx * wa.Width, "right": (1 - fx) * wa.Width}
        edge = min(dist, key=dist.get)
        length = wa.Width if edge in ("bottom", "top") else wa.Height
        t = fx if edge in ("bottom", "top") else fy
        near = min(MAGNETS, key=lambda m: abs(m - t))
        if abs(near - t) * length < PULL * self._scale():
            t = near
        half = (FLAT[0] if edge in ("bottom", "top") else UPRIGHT[1]) * self._scale() / 2
        t = min(max(t, half / length), 1 - half / length)

        self.window.run_js(f"window.hud && window.hud.dock('{edge}')")
        self._glide(self._bounds(wa, edge, t))
        self.place = {"edge": edge, "t": round(t, 4)}
        self._on_save(self.place)

    def _glide(self, target) -> None:
        """Ease the window to target over about 220 ms."""
        form = self.window.native
        x0, y0 = form.Left, form.Top
        x1, y1, w, h = target
        steps = 14
        for i in range(1, steps + 1):
            u = i / steps
            e = 1 - (1 - u) ** 3  # Ease out.
            self._set_bounds(int(x0 + (x1 - x0) * e), int(y0 + (y1 - y0) * e), w, h)
            time.sleep(0.016)
