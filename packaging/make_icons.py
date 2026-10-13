"""Draw the vox icons: a text cursor (I-beam) that sends out sound waves.

The mark says what vox does: you speak, and text appears where the cursor
is. White on near-black, one colour: it reads at any size and on any
wallpaper. Drawn in code so the icons have no source file to keep in sync.

Writes:
- assets/icon-1024.png: the large app icon, for the README
- build/vox.iconset/: every macOS icon size (build_mac.sh turns it into vox.icns)
- build/AppIcon.icon/: the same mark as an Icon Composer icon for macOS 26.
  Tahoe shrinks a plain .icns onto a grey plate; from a .icon it draws the
  plate itself (build_mac.sh compiles it into Assets.car).
- assets/vox.ico: Windows app icon
- vox/icons/menubar*.png: macOS menu bar template images (black on clear)
- vox/icons/tray-*.png: Windows tray icons, one per state
"""

import json
import math
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS = ROOT / "vox" / "icons"

CORAL = (255, 122, 89)  # Tray "listening" only.
TILE = (17, 17, 19, 255)
# A hairline keeps the dark tile apart from a dark Dock or desktop.
EDGE = (255, 255, 255, 40)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


# Beam position, as a share of the box width: the app icon centres the
# beam and its waves together; the menu bar and tray keep the old place.
APP_BEAM = 0.32


def caret(d: ImageDraw.ImageDraw, box, colour, waves: int = 2, wave_alpha=(255, 150),
          weight: float = 1.0, beam: float = 0.28) -> None:
    """Draw the I-beam with sound waves inside box = (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    stroke = w * 0.105 * weight
    # The beam sits left of centre so that the waves fit on the right.
    cx = x0 + w * (0.5 if waves == 0 else beam)
    top, bottom = y0 + h * 0.16, y1 - h * 0.16
    serif = w * 0.26
    r = stroke / 2
    d.rounded_rectangle((cx - r, top, cx + r, bottom), radius=r, fill=colour)
    d.rounded_rectangle((cx - serif / 2, top, cx + serif / 2, top + stroke), radius=r, fill=colour)
    d.rounded_rectangle((cx - serif / 2, bottom - stroke, cx + serif / 2, bottom), radius=r,
                        fill=colour)
    cy = (top + bottom) / 2
    for i in range(waves):
        rad = w * (0.17 + 0.16 * i)
        alpha = wave_alpha[min(i, len(wave_alpha) - 1)]
        c = colour[:3] + (alpha,)
        ox = cx + w * 0.11
        d.arc((ox - rad, cy - rad, ox + rad, cy + rad), -46, 46, fill=c, width=int(stroke * 0.85))


def app_icon(size: int = 1024) -> Image.Image:
    """Draw the app icon at one exact pixel size.

    Small sizes (64 px and below: the Windows taskbar, Explorer, title
    bars) keep the whole mark, so they match the large icon, with bolder
    strokes and less padding so the waves stay two clear arcs.
    """
    small = size <= 64
    k = 8 if small else 4  # Draw large and shrink, for smooth edges.
    s = size * k
    inset = round(s * (0.06 if small else 100 / 1024))
    radius = round((s - 2 * inset) * 0.225)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    ImageDraw.Draw(img).rounded_rectangle((inset, inset, s - inset, s - inset), radius=radius,
                                          fill=TILE, outline=EDGE, width=max(k, round(s / 512)))
    pad = (s - 2 * inset) * (0.10 if small else 0.14)
    box = (inset + pad, inset + pad, s - inset - pad, s - inset - pad)
    caret(ImageDraw.Draw(img), box, WHITE, wave_alpha=(255,), beam=APP_BEAM,
          weight=1.35 if small else 1.0)
    return img.resize((size, size), Image.LANCZOS)


def iconset(path: Path) -> None:
    """Write a macOS .iconset folder; iconutil turns it into vox.icns."""
    path.mkdir(parents=True, exist_ok=True)
    for pt in (16, 32, 128, 256, 512):
        app_icon(pt).save(path / f"icon_{pt}x{pt}.png")
        app_icon(pt * 2).save(path / f"icon_{pt}x{pt}@2x.png")


def icon_composer(path: Path) -> None:
    """Write an Icon Composer .icon: a dark fill and the mark as one SVG layer.

    The canvas is 1024 pt and the system masks it to its own shape, so the
    mark sits on the full canvas the way it sits on the tile in app_icon().
    """
    (path / "Assets").mkdir(parents=True, exist_ok=True)
    s = 1024
    pad = s * 0.14
    x0, y0, x1, y1 = pad, pad, s - pad, s - pad
    w, h = x1 - x0, y1 - y0
    # The same geometry as caret(), as SVG.
    stroke = w * 0.105
    r = stroke / 2
    cx = x0 + w * APP_BEAM
    top, bottom = y0 + h * 0.16, y1 - h * 0.16
    serif = w * 0.26
    cy = (top + bottom) / 2
    parts = [
        f'<rect x="{cx - r:.1f}" y="{top:.1f}" width="{stroke:.1f}" height="{bottom - top:.1f}" rx="{r:.1f}"/>',
        f'<rect x="{cx - serif / 2:.1f}" y="{top:.1f}" width="{serif:.1f}" height="{stroke:.1f}" rx="{r:.1f}"/>',
        f'<rect x="{cx - serif / 2:.1f}" y="{bottom - stroke:.1f}" width="{serif:.1f}" height="{stroke:.1f}" rx="{r:.1f}"/>',
    ]
    ox = cx + w * 0.11
    for i in range(2):
        rad = w * (0.17 + 0.16 * i)
        a = math.radians(46)
        sx, sy = ox + rad * math.cos(-a), cy + rad * math.sin(-a)
        ex, ey = ox + rad * math.cos(a), cy + rad * math.sin(a)
        parts.append(f'<path d="M{sx:.1f} {sy:.1f}A{rad:.1f} {rad:.1f} 0 0 1 {ex:.1f} {ey:.1f}" '
                     f'fill="none" stroke="#fff" stroke-width="{stroke * 0.85:.1f}"/>')
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{s}" height="{s}" viewBox="0 0 {s} {s}">'
           f'<g fill="#fff">{"".join(parts)}</g></svg>\n')
    (path / "Assets" / "mark.svg").write_text(svg, encoding="utf-8")
    fill = ",".join(f"{c / 255:.5f}" for c in TILE[:3])
    meta = {
        "fill": {"solid": f"extended-srgb:{fill},1.00000"},
        "groups": [{"layers": [{"image-name": "mark.svg", "name": "mark"}]}],
        "supported-platforms": {"squares": ["macOS"]},
    }
    (path / "icon.json").write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")


def glyph(size: int, colour, waves: int, alpha=(255, 150)) -> Image.Image:
    k = 8
    s = size * k
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    caret(ImageDraw.Draw(img), (0, 0, s, s), colour, waves, alpha)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    ICONS.mkdir(exist_ok=True)
    app_icon().save(ASSETS / "icon-1024.png")
    iconset(ROOT / "build" / "vox.iconset")
    icon_composer(ROOT / "build" / "AppIcon.icon")
    # Each .ico size drawn on its own, not scaled down from the large one.
    sizes = (16, 24, 32, 48, 64, 128, 256)
    big = app_icon(256)
    big.save(ASSETS / "vox.ico", sizes=[(n, n) for n in sizes],
             append_images=[app_icon(n) for n in sizes[:-1]])
    # Menu bar: 18 pt, drawn at 2x. Idle shows one quiet wave; listening
    # shows both waves at full strength.
    glyph(36, BLACK, 1, (110,)).save(ICONS / "menubar.png")
    glyph(36, BLACK, 2, (255, 255)).save(ICONS / "menubar-listening.png")
    tray = {"idle": (120, 120, 128, 255), "listening": CORAL + (255,),
            "busy": (59, 130, 246, 255), "error": (245, 158, 11, 255)}
    for state, colour in tray.items():
        glyph(64, colour, 2).save(ICONS / f"tray-{state}.png")


if __name__ == "__main__":
    main()
