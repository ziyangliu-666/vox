"""Drag the flow bar and dock it to a screen edge (Windows).

On Windows the bar is a small window, not a full-screen layer: a
click-through full-screen window does not mix well with WebView2. So the
window itself moves. A press on the pill calls drag(); it follows the
cursor until the left button is up, then docks to the nearest edge of the
work area (the screen without the taskbar), on a magnet at 1/3, 1/2 or
2/3 when it is close to one, and glides there.

While the button is down, a second window covers the work area and draws
the guides that hud.html draws on macOS: rails on the four edges, a dot on
each magnet, and for the target a bright rail, an alignment line and a
ghost of where the pill lands. It is drawn with GDI+, not WebView2, and
lets every click through.

Both windows are see-through by a colour key: pywebview's transparent=True
clears only the WebView2 background, and the form under it stays grey.
A colour key has no partial alpha, so hud.html drops the pill's soft
shadow on Windows (html.win).

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
LINE = 160  # Alignment line, CSS px.
KEY = (1, 1, 1)  # Colour key: pixels of this colour are see-through.
BRAND = (255, 122, 89)
RAIL = (255, 176, 156)  # Rails off the target: the brand, lighter.

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x8000000
HWND_TOPMOST = -1
SWP_FLAGS = 0x1 | 0x2 | 0x10  # NOSIZE | NOMOVE | NOACTIVATE

# Own handle to user32, so the argtypes below do not leak into pywebview's.
_user32 = ctypes.WinDLL("user32")
_user32.GetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.SetWindowLongW.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_long]
_user32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
_user32.IsWindowVisible.argtypes = [ctypes.c_void_p]
_user32.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p] + [ctypes.c_int] * 4 + [ctypes.c_uint]


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


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
    def __init__(self, window, saved, on_save, on_click) -> None:
        self.window = window
        self.place = saved if saved and saved.get("edge") else {"edge": "bottom", "t": 0.5}
        self._on_save, self._on_click = on_save, on_click
        self.pill = (60, 14)  # The pill's size in CSS px; hud.html reports it.
        self._guides = _Guides()

        def init(form):
            _see_through(form)
            # TransparencyKey makes WinForms rewrite the extended style and
            # drop the no-activate bit that pywebview set for focus=False.
            _add_ex_style(form, WS_EX_NOACTIVATE)
        self._ui(init)

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
        self.window.run_js("document.documentElement.classList.add('win')")
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
        moved, shown = False, None
        try:
            while _left_down():
                cx, cy = _cursor()
                if not moved and abs(cx - sx) + abs(cy - sy) >= CLICK:
                    moved = True
                    self.window.run_js("window.hud && window.hud.drag(true)")
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
                self.window.run_js("window.hud && window.hud.drag(false)")
        if not moved:
            self._on_click()
            return
        self._dock(_cursor())

    def _show_guides(self, target) -> None:
        state = dict(target, scale=self._scale(), pill=self.pill)
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
