"""The flow bar on Windows: a native window, drag, and dock to a screen edge.

The bar is a small layered window drawn with GDI+ (winpill.py) and put on
screen with UpdateLayeredWindow, so it has real per-pixel alpha: the
capsule's soft shadow and anti-aliased edge show over any app, and pixels
with alpha 0 let clicks through. macOS draws the bar in hud.html; Windows
does not, because a see-through WebView2 window is not reliable:
pywebview documents transparent=True as not supported on Windows,
WebView2 has no colour key (WebView2Feedback #118), and WebView2 Runtime
154 paints an opaque theme-coloured base under a transparent page
(WebView2Feedback #5752). The bar also does not need a browser.

The window itself moves. A press on the pill follows the cursor until the
left button is up, then docks to the nearest edge of the work area (the
screen without the taskbar), on a magnet at 1/3, 1/2 or 2/3 when it is
close to one, and glides there. While the button is down, a second window
covers the work area and draws the guides that hud.html draws on macOS:
rails on the four edges, a dot on each magnet, and for the target a bright
rail, an alignment line and a ghost of where the pill lands.

Threads: windows are made and moved on the WinForms UI thread (pywebview's
GUI thread) through Invoke; a render thread draws frames while something
animates; drag() runs on its own thread.

All sizes are WinForms device pixels; the CSS px sizes are scaled by the
window's DPI.
"""

import ctypes
import threading
import time
import traceback

from vox.winpill import Pill

PAD, GAP = 14, 4  # Pill to window edge; pill to screen edge.
FLAT = (360, 72)  # Window size on the bottom and top edges, CSS px.
UPRIGHT = (360, 140)  # On the left and right edges.
MAGNETS = (1 / 3, 1 / 2, 2 / 3)
PULL = 48  # CSS px
CLICK = 4  # Cursor travel below this, in device px, is a click.
LINE = 160  # Alignment line, CSS px.
KEY = (1, 1, 1)  # Guides colour key: pixels of this colour are see-through.
BRAND = (255, 122, 89)
RAIL = (255, 176, 156)  # Rails off the target: the brand, lighter.
FRAME_S = 1 / 60

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x8000000
HWND_TOPMOST = -1
SWP_FLAGS = 0x1 | 0x2 | 0x10  # NOSIZE | NOMOVE | NOACTIVATE
ULW_ALPHA, AC_SRC_ALPHA = 0x2, 0x1


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Size(ctypes.Structure):
    _fields_ = [("cx", ctypes.c_long), ("cy", ctypes.c_long)]


class _Blend(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


# Own handles to the DLLs, so the argtypes below do not leak into pywebview's.
_user32 = ctypes.WinDLL("user32")
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]
_user32.GetDC.argtypes = [ctypes.c_void_p]
_user32.GetDC.restype = ctypes.c_void_p
_user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_user32.UpdateLayeredWindow.argtypes = [
    ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(_Point), ctypes.POINTER(_Size),
    ctypes.c_void_p, ctypes.POINTER(_Point), ctypes.c_uint, ctypes.POINTER(_Blend), ctypes.c_uint]
_gdi32 = ctypes.WinDLL("gdi32")
_gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
_gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi32.SelectObject.restype = ctypes.c_void_p
_gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi32.DeleteDC.argtypes = [ctypes.c_void_p]


def _cursor() -> tuple[int, int]:
    p = _Point()
    ctypes.windll.user32.GetCursorPos(ctypes.byref(p))
    return p.x, p.y


def _left_down() -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)


def _hwnd(form) -> int:
    return form.Handle.ToInt64()


def _add_ex_style(form, bits: int) -> None:
    h = _hwnd(form)
    _user32.SetWindowLongW(h, GWL_EXSTYLE, _user32.GetWindowLongW(h, GWL_EXSTYLE) | bits)


def _put_bitmap(form, bitmap) -> None:
    """Show a 32-bit ARGB bitmap as the window's pixels (per-pixel alpha)."""
    from System.Drawing import Color

    hbmp = bitmap.GetHbitmap(Color.FromArgb(0)).ToInt64()  # Premultiplied.
    screen = _user32.GetDC(None)
    mem = _gdi32.CreateCompatibleDC(screen)
    old = _gdi32.SelectObject(mem, hbmp)
    try:
        size = _Size(bitmap.Width, bitmap.Height)
        blend = _Blend(0, 0, 255, AC_SRC_ALPHA)
        # No destination point: the window keeps its position.
        _user32.UpdateLayeredWindow(_hwnd(form), screen, None, ctypes.byref(size), mem,
                                    ctypes.byref(_Point(0, 0)), 0, ctypes.byref(blend), ULW_ALPHA)
    finally:
        _gdi32.SelectObject(mem, old)
        _gdi32.DeleteObject(hbmp)
        _gdi32.DeleteDC(mem)
        _user32.ReleaseDC(None, screen)


def _see_through(form) -> None:
    """Make pixels of the KEY colour see-through and click-through."""
    from System.Drawing import Color

    key = Color.FromArgb(*KEY)
    form.BackColor = key
    form.TransparencyKey = key


class _Guides:
    """A click-through window over the work area that draws the drag guides.

    Every method runs on the WinForms UI thread.
    """

    def __init__(self) -> None:
        self.form = None
        self.state = None

    def _make(self) -> None:
        from System.Windows.Forms import Form, FormBorderStyle, FormStartPosition

        f = Form()
        f.FormBorderStyle = getattr(FormBorderStyle, "None")
        f.ShowInTaskbar = False
        f.StartPosition = FormStartPosition.Manual
        f.TopMost = True
        _see_through(f)
        f.Paint += lambda sender, e: self._render()
        _add_ex_style(f, WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.form = f

    def show(self, state, above) -> None:
        """Draw state; on first show, put the bar window `above` back on top."""
        from System.Drawing import Rectangle

        if self.form is None:
            self._make()
        wa = state["wa"]
        b = self.form.Bounds
        if (b.X, b.Y, b.Width, b.Height) != (wa.Left, wa.Top, wa.Width, wa.Height):
            self.form.Bounds = Rectangle(wa.Left, wa.Top, wa.Width, wa.Height)
        self.state = state
        if not _user32.IsWindowVisible(_hwnd(self.form)):
            _user32.ShowWindow(_hwnd(self.form), 4)  # SW_SHOWNOACTIVATE
            _user32.SetWindowPos(_hwnd(above), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        self._render()

    def hide(self) -> None:
        if self.form is not None:
            _user32.ShowWindow(_hwnd(self.form), 0)  # SW_HIDE
        self.state = None

    def _render(self) -> None:
        from System.Drawing import BufferedGraphicsManager, Color, Rectangle

        st, f = self.state, self.form
        if st is None or f is None or f.Width <= 0 or f.Height <= 0:
            return
        screen = f.CreateGraphics()
        buf = BufferedGraphicsManager.Current.Allocate(screen, Rectangle(0, 0, f.Width, f.Height))
        try:
            g = buf.Graphics
            g.Clear(Color.FromArgb(*KEY))
            self._draw(g, st, f.Width, f.Height)
            buf.Render(screen)
        finally:
            buf.Dispose()
            screen.Dispose()

    @staticmethod
    def _draw(g, st, w, h) -> None:
        from System.Drawing import Color, Pen, SolidBrush
        from System.Drawing.Drawing2D import DashStyle, GraphicsPath, SmoothingMode

        s, edge = st["scale"], st["edge"]
        g.SmoothingMode = SmoothingMode.AntiAlias

        def px(v):
            return max(1, round(v * s))

        def anchor(e, t):
            return {"bottom": (t * w, h - GAP * s), "top": (t * w, GAP * s),
                    "left": (GAP * s, t * h), "right": (w - GAP * s, t * h)}[e]

        brand, light = Color.FromArgb(*BRAND), Color.FromArgb(*RAIL)
        white, dark = Color.FromArgb(255, 255, 255), Color.FromArgb(40, 40, 44)

        # Rails: thin and light on the four edges, thick on the target edge.
        i = px(8)
        for e in ("bottom", "top", "left", "right"):
            on = e == edge
            k = px(4 if on else 2)
            r = {"bottom": (i, h - k, w - 2 * i, k), "top": (i, 0, w - 2 * i, k),
                 "left": (0, i, k, h - 2 * i), "right": (w - k, i, k, h - 2 * i)}[e]
            brush = SolidBrush(brand if on else light)
            g.FillRectangle(brush, *r)
            brush.Dispose()

        # A dot on each magnet; the one the pill snaps to is larger.
        ring, fill = SolidBrush(brand), SolidBrush(white)
        for e in ("bottom", "top", "left", "right"):
            for m in MAGNETS:
                x, y = anchor(e, m)
                z = 1.7 if st["snapped"] and e == edge and m == st["magnet"] else 1
                ro, ri = 6 * s * z, 4 * s * z
                g.FillEllipse(ring, float(x - ro), float(y - ro), float(2 * ro), float(2 * ro))
                g.FillEllipse(fill, float(x - ri), float(y - ri), float(2 * ri), float(2 * ri))
        ring.Dispose()
        fill.Dispose()

        x, y = anchor(edge, st["t"])
        # The alignment line runs from a magnet into the screen.
        if st["snapped"]:
            pen = Pen(brand, float(px(2)))
            pen.DashStyle = DashStyle.Dash
            n = LINE * s
            end = {"bottom": (x, y - n), "top": (x, y + n), "left": (x + n, y), "right": (x - n, y)}[edge]
            g.DrawLine(pen, float(x), float(y), float(end[0]), float(end[1]))
            pen.Dispose()

        # The ghost: where the pill lands, in the orientation of that edge.
        pw, ph = st["pill"]
        if (edge in ("bottom", "top")) != (pw >= ph):
            pw, ph = ph, pw
        pw, ph = pw * s, ph * s
        gx = x if edge == "left" else x - pw if edge == "right" else x - pw / 2
        gy = y if edge == "top" else y - ph if edge == "bottom" else y - ph / 2
        d = min(pw, ph)
        path = GraphicsPath()
        path.AddArc(float(gx), float(gy), float(d), float(d), 90.0 if pw >= ph else 180.0, 180.0)
        path.AddArc(float(gx + pw - d), float(gy + ph - d), float(d), float(d),
                    270.0 if pw >= ph else 0.0, 180.0)
        path.CloseFigure()
        outline = Pen(dark, float(px(4)))
        g.DrawPath(outline, path)
        dash = Pen(white, float(px(2)))
        dash.DashStyle = DashStyle.Dash
        g.DrawPath(dash, path)
        outline.Dispose()
        dash.Dispose()
        path.Dispose()


class WinBar:
    """The flow bar window. host is any WinForms form on the UI thread."""

    def __init__(self, host, saved, on_save, on_click, on_copy) -> None:
        self.host = host
        self.form = None
        self.place = saved if saved and saved.get("edge") else {"edge": "bottom", "t": 0.5}
        self._on_save, self._on_click, self._on_copy = on_save, on_click, on_copy
        self.pill = Pill()
        self.pill.dock(self.place["edge"])
        self._guides = _Guides()
        self._size = (1, 1)  # Window size in device px; the render thread reads it.
        self._lock = threading.Lock()  # Guards pill state between threads.
        self._wake = threading.Event()
        self._dragging = False
        self._ui(self._make)
        threading.Thread(target=self._render_loop, daemon=True).start()

    # ---- WinForms helpers --------------------------------------------------

    def _ui(self, fn):
        from System import Action

        self.host.Invoke(Action(lambda: fn(self.form)))

    def _make(self, _) -> None:
        from System.Windows.Forms import (AutoScaleMode, Cursors, Form, FormBorderStyle,
                                          FormStartPosition)

        f = Form()
        f.FormBorderStyle = getattr(FormBorderStyle, "None")
        f.AutoScaleMode = getattr(AutoScaleMode, "None")
        f.ShowInTaskbar = False
        f.StartPosition = FormStartPosition.Manual
        f.TopMost = True
        f.Text = "vox flow bar"
        f.Cursor = Cursors.Hand
        f.MouseDown += self._on_down
        f.MouseEnter += lambda sender, e: self._set_hover(True)
        f.MouseLeave += lambda sender, e: self._set_hover(False)
        # Layered for UpdateLayeredWindow; no-activate, so a click on the bar
        # leaves the focus in the app the user dictates into.
        _add_ex_style(f, WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW)
        self.form = f

    def _scale(self) -> float:
        return self.form.DeviceDpi / 96

    def _work_area(self, point=None):
        from System.Drawing import Point
        from System.Windows.Forms import Screen

        if point is None:
            return Screen.FromControl(self.form).WorkingArea
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
        resized = (w, h) != self._size

        def fn(form):
            from System.Drawing import Rectangle

            form.Bounds = Rectangle(x, y, w, h)
            if resized:
                self._size = (w, h)
                self._render(form)
        self._ui(fn)

    # ---- the pill ------------------------------------------------------------

    def set(self, payload: dict) -> None:
        """Show a phase: the payload hud.html's window.hud.set() takes."""
        with self._lock:
            self.pill.set(payload)
        self._wake.set()

    def level(self, x: float) -> None:
        with self._lock:
            self.pill.level = max(0.0, min(1.0, float(x)))

    @property
    def hovered(self) -> bool:
        return self.pill.hover

    def _set_hover(self, on: bool) -> None:
        with self._lock:
            self.pill.hover = on
        self._wake.set()

    def _render_loop(self) -> None:
        while True:
            with self._lock:
                moving = self.pill.moving()
            if not moving:
                self._wake.wait(1.0)
                self._wake.clear()
            try:
                self._ui(self._render)
            except Exception:
                traceback.print_exc()
                print("vox: flow bar render failed", flush=True)
                time.sleep(1)
            time.sleep(FRAME_S)

    def _render(self, form) -> None:
        from System.Drawing import Bitmap, Color, Graphics
        from System.Drawing.Imaging import PixelFormat

        w, h = self._size
        s = self._scale()
        bmp = Bitmap(w, h, PixelFormat.Format32bppArgb)
        g = Graphics.FromImage(bmp)
        try:
            g.Clear(Color.FromArgb(0, 0, 0, 0))
            with self._lock:
                self.pill.step()
                self.pill.draw(g, w / s, h / s, s)
            _put_bitmap(form, bmp)
            if not _user32.IsWindowVisible(_hwnd(form)):
                _user32.ShowWindow(_hwnd(form), 4)  # SW_SHOWNOACTIVATE
                _user32.SetWindowPos(_hwnd(form), HWND_TOPMOST, 0, 0, 0, 0, SWP_FLAGS)
        finally:
            g.Dispose()
            bmp.Dispose()

    # ---- placement ---------------------------------------------------------

    def restore(self) -> None:
        edge = self.place["edge"]
        with self._lock:
            self.pill.dock(edge)
        self._set_bounds(*self._bounds(self._work_area(), edge, self.place.get("t", 0.5)))
        self._wake.set()

    def reset(self) -> None:
        self.place = {"edge": "bottom", "t": 0.5}
        self._on_save(None)
        self.restore()

    # ---- drag --------------------------------------------------------------

    def _on_down(self, sender, e) -> None:
        from System.Windows.Forms import MouseButtons

        if e.Button != MouseButtons.Left or self._dragging:
            return
        with self._lock:
            hidden = self.pill.phase == "hidden"
        if hidden:
            return
        self._dragging = True
        # drag() waits on the UI thread through Invoke; it must not run on it.
        threading.Thread(target=self.drag, daemon=True).start()

    def drag(self) -> None:
        """Follow the cursor while the left button is down; then dock or click."""
        try:
            self._drag()
        finally:
            self._dragging = False

    def _drag(self) -> None:
        sx, sy = _cursor()
        form = self.form
        ox, oy, w, h = form.Left, form.Top, form.Width, form.Height
        moved, shown = False, None
        try:
            while _left_down():
                cx, cy = _cursor()
                if not moved and abs(cx - sx) + abs(cy - sy) >= CLICK:
                    moved = True
                    with self._lock:
                        self.pill.dragging = True
                    self._wake.set()
                if moved:
                    self._set_bounds(ox + cx - sx, oy + cy - sy, w, h)
                    target = self._target((cx, cy))
                    key = (target["edge"], round(target["t"], 3), target["snapped"],
                           target["wa"].Left, target["wa"].Top)
                    if key != shown:
                        shown = key
                        self._show_guides(target)
                time.sleep(0.008)
        finally:
            if moved:
                self._hide_guides()
                with self._lock:
                    self.pill.dragging = False
                self._wake.set()
        if not moved:
            s = self._scale()
            with self._lock:
                copy = self.pill.hit_copy((sx - ox) / s, (sy - oy) / s, w / s, h / s)
            (self._on_copy if copy else self._on_click)()
            return
        self._dock(_cursor())

    def _show_guides(self, target) -> None:
        with self._lock:
            size = (self.pill.w, self.pill.h)
        state = dict(target, scale=self._scale(), pill=size)
        try:
            self._ui(lambda form: self._guides.show(state, above=form))
        except Exception as e:  # The guides are a nicety; the drag goes on.
            print(f"vox: flow bar guides: {e}", flush=True)

    def _hide_guides(self) -> None:
        try:
            self._ui(lambda form: self._guides.hide())
        except Exception as e:
            print(f"vox: flow bar guides: {e}", flush=True)

    def _target(self, cursor) -> dict:
        """Where a drop at cursor lands: the nearest edge, then a magnet on it."""
        wa = self._work_area(cursor)
        fx = min(max((cursor[0] - wa.Left) / wa.Width, 0), 1)
        fy = min(max((cursor[1] - wa.Top) / wa.Height, 0), 1)  # 0 at the top.
        dist = {"bottom": (1 - fy) * wa.Height, "top": fy * wa.Height,
                "left": fx * wa.Width, "right": (1 - fx) * wa.Width}
        edge = min(dist, key=dist.get)
        length = wa.Width if edge in ("bottom", "top") else wa.Height
        t = fx if edge in ("bottom", "top") else fy
        near = min(MAGNETS, key=lambda m: abs(m - t))
        snapped = abs(near - t) * length < PULL * self._scale()
        if snapped:
            t = near
        half = (FLAT[0] if edge in ("bottom", "top") else UPRIGHT[1]) * self._scale() / 2
        t = min(max(t, half / length), 1 - half / length)
        return {"wa": wa, "edge": edge, "t": t, "snapped": snapped, "magnet": near}

    def _dock(self, cursor) -> None:
        target = self._target(cursor)
        wa, edge, t = target["wa"], target["edge"], target["t"]
        with self._lock:
            self.pill.dock(edge)
        self._wake.set()
        self._glide(self._bounds(wa, edge, t))
        self.place = {"edge": edge, "t": round(t, 4)}
        self._on_save(self.place)

    def _glide(self, target) -> None:
        """Ease the window to target over about 220 ms."""
        x0, y0 = self.form.Left, self.form.Top
        x1, y1, w, h = target
        steps = 14
        for i in range(1, steps + 1):
            u = i / steps
            e = 1 - (1 - u) ** 3  # Ease out.
            self._set_bounds(int(x0 + (x1 - x0) * e), int(y0 + (y1 - y0) * e), w, h)
            time.sleep(0.016)
