"""Draw the vox icons: a text cursor (I-beam) that sends out sound waves.

The mark says what vox does: you speak, and text appears where the cursor
is. Drawn in code so the icons have no source file to keep in sync.

Writes:
- assets/icon-1024.png: the large app icon, for the README
- build/vox.iconset/: every macOS icon size (build_mac.sh turns it into vox.icns)
- assets/vox.ico: Windows app icon
- vox/icons/menubar*.png: macOS menu bar template images (black on clear)
- vox/icons/tray-*.png: Windows tray icons, one per state
"""

from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
ICONS = ROOT / "vox" / "icons"

CORAL, PINK = (255, 122, 89), (236, 72, 120)
WHITE = (255, 255, 255, 255)
BLACK = (0, 0, 0, 255)


def caret(d: ImageDraw.ImageDraw, box, colour, waves: int = 2, wave_alpha=(255, 150),
          weight: float = 1.0) -> None:
    """Draw the I-beam with sound waves inside box = (x0, y0, x1, y1)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    stroke = w * 0.105 * weight
    # The beam sits left of centre so that the waves fit on the right.
    cx = x0 + w * (0.5 if waves == 0 else 0.28)
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

    Below 128 px the full mark turns to mush, so small sizes use a bolder
    beam alone and less padding, the way system icons do: at 64 px and
    below a wave is only a dot.
    """
    small = size <= 64
    k = 8 if small else 4  # Draw large and shrink, for smooth edges.
    s = size * k
    inset = round(s * (0.06 if small else 100 / 1024))
    radius = round((s - 2 * inset) * 0.225)
    grad = Image.new("RGBA", (s, s))
    gd = ImageDraw.Draw(grad)
    for y in range(s):
        t = y / s
        gd.line([(0, y), (s, y)],
                fill=tuple(int(CORAL[i] + (PINK[i] - CORAL[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((inset, inset, s - inset, s - inset), radius=radius,
                                           fill=255)
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    pad = (s - 2 * inset) * (0.10 if small else 0.14)
    box = (inset + pad, inset + pad, s - inset - pad, s - inset - pad)
    if small:
        caret(ImageDraw.Draw(img), box, WHITE, waves=0, weight=1.8)
    else:
        caret(ImageDraw.Draw(img), box, WHITE)
    return img.resize((size, size), Image.LANCZOS)


def iconset(path: Path) -> None:
    """Write a macOS .iconset folder; iconutil turns it into vox.icns."""
    path.mkdir(parents=True, exist_ok=True)
    for pt in (16, 32, 128, 256, 512):
        app_icon(pt).save(path / f"icon_{pt}x{pt}.png")
        app_icon(pt * 2).save(path / f"icon_{pt}x{pt}@2x.png")


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
