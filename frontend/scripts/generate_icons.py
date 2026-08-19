"""Draw the home-screen icons.

Run by hand, not by the build:

    python frontend/scripts/generate_icons.py

Kept in the repo rather than the icons being pasted in as opaque binaries, so
the shape can be changed later by editing a few numbers instead of by opening
an image editor and guessing at the original colours.

Pure standard library on purpose. Pillow is one line of code away but it would
be a dependency the frontend does not otherwise have, installed on every
machine that touches this project, for four files that change approximately
never.

WHY THERE ARE ICONS AT ALL: on iPhone, Web Push only works for a site added to
the Home Screen. That makes "add to Home Screen" a prerequisite for this
product working on an iPhone at all, not a nicety -- and an app the owner is
asked to keep on their home screen should not be a grey square with a cropped
screenshot in it.
"""

import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "public" / "icons"

# Rendered once at this size and downsampled, which is both faster than
# rasterising four times and smoother than any of them would be alone.
BASE = 1024

BACKGROUND = (15, 23, 42)  # slate-900, the app's own page colour
LINE = (52, 211, 153)  # emerald-400
ALERT = (251, 191, 36)  # amber-400 -- the moment the thing fires

# An ascending line with a real dip in it. A monotonic line reads as a logo;
# this reads as a price. Coordinates are fractions of the canvas so the shape
# is independent of BASE.
POINTS = [(0.14, 0.70), (0.32, 0.52), (0.46, 0.62), (0.64, 0.36), (0.80, 0.26)]
LINE_WIDTH = 0.075
ALERT_RADIUS = 0.085


def _distance_to_segment(px, py, ax, ay, bx, by):
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq == 0:
        return math.hypot(px - ax, py - ay)
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / length_sq))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def _blend(canvas, size, cx, cy, radius, colour):
    """Stamp a filled disc, softened at the rim so the downsample has
    something to work with rather than a staircase."""
    lo_x, hi_x = max(0, int(cx - radius - 2)), min(size, int(cx + radius + 3))
    lo_y, hi_y = max(0, int(cy - radius - 2)), min(size, int(cy + radius + 3))
    for y in range(lo_y, hi_y):
        row = canvas[y]
        for x in range(lo_x, hi_x):
            d = math.hypot(x + 0.5 - cx, y + 0.5 - cy)
            if d > radius + 1:
                continue
            alpha = 1.0 if d <= radius else (radius + 1 - d)
            base = row[x]
            row[x] = tuple(round(b + (c - b) * alpha) for b, c in zip(base, colour))


def _stroke(canvas, size, points, width, colour):
    half = width / 2
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        lo_x = max(0, int(min(ax, bx) - half - 2))
        hi_x = min(size, int(max(ax, bx) + half + 3))
        lo_y = max(0, int(min(ay, by) - half - 2))
        hi_y = min(size, int(max(ay, by) + half + 3))
        for y in range(lo_y, hi_y):
            row = canvas[y]
            for x in range(lo_x, hi_x):
                d = _distance_to_segment(x + 0.5, y + 0.5, ax, ay, bx, by)
                if d > half + 1:
                    continue
                alpha = 1.0 if d <= half else (half + 1 - d)
                base = row[x]
                row[x] = tuple(round(b + (c - b) * alpha) for b, c in zip(base, colour))


def render(size: int, inset: float = 0.0) -> list[list[tuple[int, int, int]]]:
    """`inset` shrinks the artwork towards the centre, leaving the background
    to be cropped. Android's maskable icons can be trimmed to a circle by the
    launcher, and a chart line that runs to the edge loses its ends."""
    canvas = [[BACKGROUND] * size for _ in range(size)]

    def place(fx, fy):
        span = 1 - 2 * inset
        return ((inset + fx * span) * size, (inset + fy * span) * size)

    scale = (1 - 2 * inset) * size
    points = [place(x, y) for x, y in POINTS]
    _stroke(canvas, size, points, LINE_WIDTH * scale, LINE)
    _blend(canvas, size, points[-1][0], points[-1][1], ALERT_RADIUS * scale, ALERT)
    return canvas


def downsample(canvas, size, target):
    """Box filter. The rasteriser already softens edges; this is what turns
    that into something that looks deliberate at 60 pixels on a phone."""
    if size == target:
        return canvas
    factor = size / target
    out = []
    for y in range(target):
        y0, y1 = int(y * factor), int((y + 1) * factor)
        row = []
        for x in range(target):
            x0, x1 = int(x * factor), int((x + 1) * factor)
            r = g = b = n = 0
            for sy in range(y0, y1):
                source = canvas[sy]
                for sx in range(x0, x1):
                    pr, pg, pb = source[sx]
                    r += pr
                    g += pg
                    b += pb
                    n += 1
            row.append((r // n, g // n, b // n))
        out.append(row)
    return out


def write_png(path: Path, rows) -> None:
    size = len(rows)
    raw = bytearray()
    for row in rows:
        raw.append(0)  # filter type 0: none. The image is tiny either way.
        for r, g, b in row:
            raw += bytes((r, g, b))

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + tag
            + payload
            + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
        )

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    path.write_bytes(png)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)

    full = render(BASE)
    for size, name in ((512, "icon-512.png"), (192, "icon-192.png"), (180, "apple-touch-icon.png")):
        write_png(OUT / name, downsample(full, BASE, size))
        print(f"wrote {name}")

    # Android may crop this to a circle; the safe zone is the middle 80%.
    masked = render(BASE, inset=0.14)
    write_png(OUT / "icon-maskable-512.png", downsample(masked, BASE, 512))
    print("wrote icon-maskable-512.png")


if __name__ == "__main__":
    main()
