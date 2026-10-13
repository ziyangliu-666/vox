"""Draw the flow bar pill with GDI+ (Windows).

hud.html draws the pill on macOS. On Windows the pill is drawn here into a
32-bit bitmap that winbar.py puts on screen with UpdateLayeredWindow, so it
has real per-pixel alpha: a soft shadow, anti-aliased edges, and pixels with
alpha 0 that let clicks through. This file mirrors hud.html: the same
phases, sizes, colours and bar motion. Sizes are CSS px; draw() scales them.

Pill.step() advances the animation; Pill.draw() paints the current frame.
Both run on one thread (winbar.py's render thread).
"""

import math
import time

N = 12  # Level bars.
IDLE = (46, 10)
IDLE_HOVER = (60, 14)
H = 32  # Pill height with content.
CAP = 332  # Widest pill: the 360 px window minus room for the shadow.
PAD = 14  # Pill to the window edge it is docked to.

BG = (240, 22, 22, 25)  # hud.html --hud-bg, alpha first.
BG_IDLE = (184, 28, 28, 32)
EDGE = (36, 255, 255, 255)
EDGE_IDLE = (97, 255, 255, 255)
FG = (255, 255, 255, 255)
FG2 = (168, 255, 255, 255)
FG3 = (115, 255, 255, 255)
CHIP = (36, 255, 255, 255)
OK = (255, 52, 209, 122)
WARN = (255, 255, 181, 71)
X = (128, 255, 255, 255)
DARK = (255, 17, 17, 17)

# Bar envelope: centre bars are taller than edge bars (hud.html env).
ENV = [0.4 + 0.6 * math.sin(math.pi * (i + 0.5) / N) for i in range(N)]


def _ease(cur: float, target: float, dt: float, tau: float = 0.05) -> float:
    return target + (cur - target) * math.exp(-dt / tau)


class Pill:
    def __init__(self) -> None:
        self.payload = {"phase": "hidden"}
        self.phase = "hidden"
        self.edge = "bottom"
        self.hover = False
        self.dragging = False
        self.level = 0.0  # Last level from Python, 0..1.
        self._shown = 0.0  # Smoothed level.
        # Animated values: size, opacity, scale, content fade.
        self.w, self.h = IDLE
        self.alpha = 0.0
        self.scale = 1.0
        self.fade = 0.0
        self._t = time.perf_counter()
        self._layout = None
        self._fonts = None

    # ---- state ---------------------------------------------------------------

    def set(self, payload: dict) -> None:
        phase = payload.get("phase") or "hidden"
        if phase != self.phase and phase not in ("idle", "hidden"):
            self.fade = 0.0
        if phase == "listening" and self.phase != "listening":
            self._shown = self.level = 0.0
        self.payload, self.phase = payload, phase
        self._layout = None

    def dock(self, edge: str) -> None:
        self.edge = edge
        self._layout = None

    @property
    def upright(self) -> bool:
        return self.edge in ("left", "right") and self.phase in (
            "idle", "hidden", "listening", "transcribing", "cancelled")

    def moving(self) -> bool:
        """True while a frame differs from the last: animate on."""
        if self.phase in ("listening", "transcribing"):
            return True
        tw, th, ta, ts = self._targets()
        return (abs(self.w - tw) > 0.2 or abs(self.h - th) > 0.2 or abs(self.alpha - ta) > 0.01
                or abs(self.scale - ts) > 0.002 or self.fade < 0.99)

    def step(self) -> None:
        now = time.perf_counter()
        dt, self._t = min(now - self._t, 0.1), now
        tw, th, ta, ts = self._targets()
        self.w, self.h = _ease(self.w, tw, dt), _ease(self.h, th, dt)
        self.alpha = _ease(self.alpha, ta, dt, 0.04)
        self.scale = _ease(self.scale, ts, dt, 0.05)
        self.fade = min(1.0, self.fade + dt / 0.14)
        # Fast attack, slow release (hud.html frame()).
        k = 0.55 if self.level > self._shown else 0.18
        self._shown += (self.level - self._shown) * min(1.0, k * dt * 60)

    def _targets(self):
        """Target width, height, opacity and scale for the current state."""
        p = self.phase
        scale = 1.1 if self.dragging else 1.06 if self.hover else 1.0
        if p in ("idle", "hidden"):
            w, h = IDLE_HOVER if self.hover or self.dragging else IDLE
            if self.upright:
                w, h = h, w
            alpha = 0.0 if p == "hidden" else 1.0 if self.hover or self.dragging else 0.9
            return w, h, alpha, scale
        if self.upright:
            if p == "cancelled":
                return H, H, 1.0, scale
            return H, 104 if self.payload.get("locked") else 84, 1.0, scale
        return self._lay()["width"], H, 1.0, scale

    # ---- layout --------------------------------------------------------------

    def _font(self, g, size, s):
        from System.Drawing import Font, FontFamily, FontStyle, GraphicsUnit

        if self._fonts is None:
            names = {f.Name for f in FontFamily.Families}
            # YaHei UI covers Latin and Chinese; GDI+ does little font fallback.
            self._fonts = "Microsoft YaHei UI" if "Microsoft YaHei UI" in names else "Segoe UI"
        return Font(self._fonts, float(size * s), FontStyle.Regular, GraphicsUnit.Pixel)

    @staticmethod
    def _fmt():
        from System.Drawing import StringFormat, StringFormatFlags

        f = StringFormat(StringFormat.GenericTypographic)
        f.FormatFlags = f.FormatFlags | StringFormatFlags.MeasureTrailingSpaces | StringFormatFlags.NoWrap
        return f

    def _measure(self, text: str, size: float) -> float:
        """Width of text in CSS px."""
        from System.Drawing import Bitmap, Graphics

        bmp = Bitmap(1, 1)
        g = Graphics.FromImage(bmp)
        font = self._font(g, size, 4)  # Measure large, then scale down: less rounding.
        w = g.MeasureString(text, font, 100000, self._fmt()).Width / 4
        font.Dispose()
        g.Dispose()
        bmp.Dispose()
        return w

    def _fit_head(self, text: str, avail: float) -> str:
        """Keep the END of the text: "…the last words" (hud.html fitHead)."""
        if self._measure(text, 13) <= avail:
            return text
        chars = list(text)
        lo, hi = 1, len(chars)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._measure("…" + "".join(chars[mid:]), 13) <= avail:
                hi = mid
            else:
                lo = mid + 1
        return "…" + "".join(chars[lo:])

    def _lay(self) -> dict:
        """Horizontal content: a list of (kind, width, data), and the width."""
        if self._layout is not None:
            return self._layout
        p, pay = self.phase, self.payload
        items, gap = [], 8
        if p == "listening":
            locked = bool(pay.get("locked"))
            toggle = pay.get("mode") == "toggle"
            n = N - (6 if locked else 4 if toggle else 0)
            if locked:
                gap = 6
                items.append(("lock", 12, None))
            items.append(("bars", n * 3 + (n - 1) * 2, n))
            if toggle:
                key = pay.get("key")
                inner = 3 if locked else 4
                parts = ([("chip", key), ("text", "to stop ·"), ("chip", "esc"), ("text", "to cancel")]
                         if key else [("text", "Press again to stop")])
                sized = [(k, t, self._measure(t, 11) + 10 if k == "chip" else self._measure(t, 12))
                         for k, t in parts]
                width = sum(w for _, _, w in sized) + inner * (len(sized) - 1)
                items.append(("hint", width, (sized, inner)))
        elif p == "transcribing":
            items.append(("bars", N * 3 + (N - 1) * 2, N))
        elif p == "done":
            out = pay.get("output")
            tag = "Copied" if out == "copy" else "Pasted" if out == "paste" else ""
            fixed = 24 + 15 + gap + (gap + self._measure(tag, 11) if tag else 0)
            text = " ".join((pay.get("text") or "").split()) or "Done"
            text = self._fit_head(text, CAP - fixed)
            items += [("ok", 15, None), ("text", self._measure(text, 13), text)]
            if tag:
                items.append(("tag", self._measure(tag, 11), tag))
        elif p == "error":
            text = pay.get("message") or "Something went wrong"
            avail = CAP - (24 + 15 + gap)
            if self._measure(text, 13) > avail:
                chars = list(text)
                while chars and self._measure("".join(chars) + "…", 13) > avail:
                    chars.pop()
                text = "".join(chars) + "…"
            items += [("warn", 15, None), ("text", self._measure(text, 13), text)]
        elif p == "cancelled":
            items.append(("x", 15, None))
        width = sum(w for _, w, _ in items) + gap * max(0, len(items) - 1) + 24
        self._layout = {"items": items, "gap": gap, "width": min(CAP, math.ceil(width))}
        return self._layout

    # ---- drawing -------------------------------------------------------------

    def rect(self, ww: float, wh: float):
        """The pill's box in the window, CSS px, before the scale."""
        w, h = self.w, self.h
        if self.edge == "bottom":
            return (ww - w) / 2, wh - PAD - h, w, h
        if self.edge == "top":
            return (ww - w) / 2, PAD, w, h
        if self.edge == "left":
            return PAD, (wh - h) / 2, w, h
        return ww - PAD - w, (wh - h) / 2, w, h

    def draw(self, g, ww: float, wh: float, s: float) -> None:
        """Paint the frame on g, a cleared bitmap of ww x wh CSS px at scale s."""
        from System.Drawing import Color
        from System.Drawing.Drawing2D import SmoothingMode
        from System.Drawing.Text import TextRenderingHint

        if self.alpha < 0.01:
            return
        g.SmoothingMode = SmoothingMode.AntiAlias
        g.TextRenderingHint = TextRenderingHint.AntiAliasGridFit
        x, y, w, h = self.rect(ww, wh)
        # Scale about the pill's centre, then work in device px.
        cx, cy = x + w / 2, y + h / 2
        w, h = w * self.scale, h * self.scale
        x, y = (cx - w / 2) * s, (cy - h / 2) * s
        w, h = w * s, h * s
        a = self.alpha
        idle = self.phase in ("idle", "hidden")

        def col(c, k=1.0):
            return Color.FromArgb(max(0, min(255, int(c[0] * a * k))), c[1], c[2], c[3])

        r = min(w, h) / 2
        # Shadow: hud.html 0 8px 24px .28 + 0 2px 6px .18 (idle: 0 2px 6px .22).
        if idle:
            _shadow(g, x, y, w, h, r, 2 * s, 6 * s, 0.22 * a)
        else:
            _shadow(g, x, y, w, h, r, 8 * s, 24 * s, 0.28 * a)
            _shadow(g, x, y, w, h, r, 2 * s, 6 * s, 0.18 * a)
        _fill(g, _capsule(x, y, w, h), col(BG_IDLE if idle else BG))
        _stroke(g, _capsule(x, y, w, h), col(EDGE_IDLE if idle else EDGE), max(1.0, s))

        if idle:
            return
        # Content fades in after a phase change, clipped to the pill.
        k = self.fade
        path = _capsule(x, y, w, h)
        g.SetClip(path)
        z = s * self.scale
        if self.upright:
            self._draw_column(g, x, y, w, h, z, lambda c: col(c, k))
        else:
            self._draw_row(g, x, y, w, h, z, lambda c: col(c, k))
        g.ResetClip()
        path.Dispose()

    def _bar_len(self, i: int) -> float:
        """Length of bar i in CSS px (hud.html frame())."""
        t = time.perf_counter() * 1000
        if self.phase == "listening":
            lv = min(1.0, self._shown * 1.35)
            breathe = 3 * (0.5 + 0.5 * math.sin(t * 0.0035 + i * 0.55))
            wob = 0.72 + 0.28 * math.sin(t * 0.011 + i * 1.9)
            return 4 + breathe + 14 * lv * ENV[i] * wob
        return 4 + 6 * (0.5 + 0.5 * math.sin(t * 0.009 - i * 0.65))

    def _draw_row(self, g, x, y, w, h, z, col) -> None:
        lay = self._lay()
        total = sum(iw for _, iw, _ in lay["items"]) + lay["gap"] * max(0, len(lay["items"]) - 1)
        px, cy = x + (w - total * z) / 2, y + h / 2
        for kind, iw, data in lay["items"]:
            self._item(g, kind, data, px, cy, iw * z, z, col)
            px += (iw + lay["gap"]) * z

    def _draw_column(self, g, x, y, w, h, z, col) -> None:
        cx = x + w / 2
        if self.phase == "cancelled":
            self._item(g, "x", None, cx - 7.5 * z, y + h / 2, 15 * z, z, col)
            return
        top = y + 12 * z
        if self.payload.get("locked") and self.phase == "listening":
            self._item(g, "lock", None, cx - 6 * z, top + 6 * z, 12 * z, z, col)
            top += 20 * z
        brush = _brush(col(FG if self.phase == "listening" else (204, 255, 255, 255)))
        for i in range(N):
            ln = min(self._bar_len(i), 21) * z
            _fill_round(g, brush, cx - ln / 2, top + i * 5 * z, ln, 3 * z, 1.5 * z)
        brush.Dispose()

    def _item(self, g, kind, data, x, cy, w, z, col) -> None:
        from System.Drawing import PointF
        from System.Drawing.Drawing2D import GraphicsPath, LineCap, LineJoin

        if kind == "bars":
            brush = _brush(col(FG if self.phase == "listening" else (204, 255, 255, 255)))
            for i in range(data):
                j = i + (N - data) // 2  # Dropped bars come off both ends.
                ln = self._bar_len(j) * z
                _fill_round(g, brush, x + i * 5 * z, cy - ln / 2, 3 * z, ln, 1.5 * z)
            brush.Dispose()
        elif kind in ("text", "tag"):
            size, c = (13, FG) if kind == "text" else (11, FG3)
            self._text(g, data, size, z, x, cy, col(c))
        elif kind == "hint":
            parts, inner = data
            px = x
            for k, t, pw in parts:
                if k == "chip":
                    brush = _brush(col(CHIP))
                    _fill_round(g, brush, px, cy - 9 * z, pw * z, 18 * z, 4 * z)
                    brush.Dispose()
                    self._text(g, t, 11, z, px + 5 * z, cy, col(FG))
                else:
                    self._text(g, t, 12, z, px, cy, col(FG2))
                px += (pw + inner) * z
        elif kind == "lock":
            c = col(FG2)
            s = 12 * z / 12
            brush = _brush(c)
            _fill_round(g, brush, x + 2.2 * s, cy - 6 * s + 5.2 * s, 7.6 * s, 5.6 * s, 1.4 * s)
            brush.Dispose()
            pen = _pen(c, 1.3 * s)
            g.DrawArc(pen, float(x + 3.9 * s), float(cy - 6 * s + 1.8 * s), float(4.2 * s), float(4.2 * s), 180.0, 180.0)
            g.DrawLine(pen, float(x + 3.9 * s), float(cy - 6 * s + 3.9 * s), float(x + 3.9 * s), float(cy - 6 * s + 5.2 * s))
            g.DrawLine(pen, float(x + 8.1 * s), float(cy - 6 * s + 3.9 * s), float(x + 8.1 * s), float(cy - 6 * s + 5.2 * s))
            pen.Dispose()
        elif kind in ("ok", "x"):
            s = w / 16
            top = cy - 8 * s
            brush = _brush(col(OK if kind == "ok" else X))
            g.FillEllipse(brush, float(x + s), float(top + s), float(14 * s), float(14 * s))
            brush.Dispose()
            pen = _pen(col(DARK), 1.7 * s if kind == "ok" else 1.6 * s)
            pen.StartCap = pen.EndCap = LineCap.Round
            pen.LineJoin = LineJoin.Round
            if kind == "ok":
                pts = [PointF(float(x + px * s), float(top + py * s)) for px, py in ((4.8, 8.3), (6.9, 10.4), (11.2, 5.8))]
                g.DrawLines(pen, pts)
            else:
                g.DrawLine(pen, float(x + 5.6 * s), float(top + 5.6 * s), float(x + 10.4 * s), float(top + 10.4 * s))
                g.DrawLine(pen, float(x + 10.4 * s), float(top + 5.6 * s), float(x + 5.6 * s), float(top + 10.4 * s))
            pen.Dispose()
        elif kind == "warn":
            s = w / 16
            top = cy - 8 * s
            path = GraphicsPath()
            path.AddPolygon([PointF(float(x + px * s), float(top + py * s)) for px, py in ((8, 1.8), (14.6, 13.4), (1.4, 13.4))])
            _fill(g, path, col(WARN))
            path.Dispose()
            pen = _pen(col(DARK), 1.6 * s)
            pen.StartCap = pen.EndCap = LineCap.Round
            g.DrawLine(pen, float(x + 8 * s), float(top + 6.3 * s), float(x + 8 * s), float(top + 9.5 * s))
            pen.Dispose()
            brush = _brush(col(DARK))
            g.FillEllipse(brush, float(x + 7.1 * s), float(top + 10.5 * s), float(1.8 * s), float(1.8 * s))
            brush.Dispose()

    def _text(self, g, text, size, z, x, cy, color) -> None:
        from System.Drawing import PointF

        font = self._font(g, size, z)
        fmt = self._fmt()
        h = font.GetHeight(g)
        brush = _brush(color)
        g.DrawString(text, font, brush, PointF(float(x), float(cy - h / 2)), fmt)
        brush.Dispose()
        font.Dispose()


# ---- GDI+ helpers -----------------------------------------------------------

def _brush(color):
    from System.Drawing import SolidBrush

    return SolidBrush(color)


def _pen(color, width):
    from System.Drawing import Pen

    return Pen(color, float(width))


def _round(x, y, w, h, r):
    from System.Drawing.Drawing2D import GraphicsPath

    r = max(0.01, min(r, w / 2, h / 2))
    d = 2 * r
    p = GraphicsPath()
    p.AddArc(float(x), float(y), float(d), float(d), 180.0, 90.0)
    p.AddArc(float(x + w - d), float(y), float(d), float(d), 270.0, 90.0)
    p.AddArc(float(x + w - d), float(y + h - d), float(d), float(d), 0.0, 90.0)
    p.AddArc(float(x), float(y + h - d), float(d), float(d), 90.0, 90.0)
    p.CloseFigure()
    return p


def _capsule(x, y, w, h):
    return _round(x, y, w, h, min(w, h) / 2)


def _fill(g, path, color) -> None:
    brush = _brush(color)
    g.FillPath(brush, path)
    brush.Dispose()


def _fill_round(g, brush, x, y, w, h, r) -> None:
    path = _round(x, y, w, h, r)
    g.FillPath(brush, path)
    path.Dispose()


def _stroke(g, path, color, width) -> None:
    pen = _pen(color, width)
    g.DrawPath(pen, path)
    pen.Dispose()


def _shadow(g, x, y, w, h, r, dy, blur, alpha) -> None:
    """A soft shadow, a cheap stand-in for a CSS blur.

    Thin layers from blur/2 inside the shape to blur/2 outside it: they pile
    up to the full alpha in the middle and thin out across the edge.
    """
    from System.Drawing import Color

    steps = 12
    k = max(1, min(255, round(255 * alpha / steps)))
    for i in range(steps):
        grow = blur * (i + 0.5) / steps - blur / 2
        if w + 2 * grow <= 0 or h + 2 * grow <= 0:
            continue
        path = _round(x - grow, y + dy - grow, w + 2 * grow, h + 2 * grow, max(0.5, r + grow))
        _fill(g, path, Color.FromArgb(k, 0, 0, 0))
        path.Dispose()
