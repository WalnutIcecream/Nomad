"""Generate assets/nomad.ico and assets/nomad.png from code.

Requires Pillow. The generated files are committed, so this only needs to run
when the icon should change.

    python scripts/app/make_icon.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parent.parent.parent
ASSETS = ROOT / "assets"
BG = (20, 22, 31, 255)
BLUE = (79, 156, 247, 255)
GREEN = (90, 209, 160, 255)


def _cube(draw: ImageDraw.ImageDraw, s: float, ox: float, oy: float, color) -> None:
    """One isometric cube with its front-bottom corner at (ox, oy)."""
    u = 30.0 * s
    right, left, up = (u, u * 0.55), (-u, u * 0.55), (0.0, -u)

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1])

    f = (ox, oy)
    fr, fl, ft = add(f, right), add(f, left), add(f, up)
    frt, flt, far = add(fr, up), add(fl, up), add(fr, left)
    dark = tuple(c * 3 // 5 for c in color[:3]) + (255,)
    mid = tuple(c * 4 // 5 for c in color[:3]) + (255,)
    draw.polygon([f, fr, frt, ft], fill=dark)
    draw.polygon([f, ft, flt, fl], fill=mid)
    draw.polygon([ft, frt, far, flt], fill=color)


def build(size: int) -> Image.Image:
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(canvas).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=size * 0.22, fill=BG
    )
    overlay = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    s = size / 256.0
    cx, cy = size * 0.5, size * 0.6
    u = 30.0 * s
    f1 = (cx - 46 * s, cy)
    f2 = (f1[0] + u, f1[1] - u * 0.45)
    _cube(draw, s, f1[0], f1[1], GREEN)
    _cube(draw, s, f2[0], f2[1], BLUE)
    canvas.alpha_composite(overlay)
    return canvas


def main() -> int:
    ASSETS.mkdir(parents=True, exist_ok=True)
    master = build(512)
    master.save(ASSETS / "nomad.png")
    master.resize((256, 256), Image.LANCZOS).save(
        ASSETS / "nomad.ico", format="ICO", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)]
    )
    print(f"wrote {ASSETS / 'nomad.ico'} and {ASSETS / 'nomad.png'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())