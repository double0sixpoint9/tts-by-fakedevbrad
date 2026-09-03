"""
Generate icon.ico (and icon.svg) for the desktop shortcut.

Draws the synthwave mark procedurally and writes a multi-resolution ICO using
nothing but the standard library: a small PNG encoder over zlib, wrapped in an
ICO container. No Pillow, no install step.

    python make_icon.py
"""

from __future__ import annotations

import math
import os
import struct
import zlib

SIZES = (16, 32, 48, 64, 128, 256)
SUPERSAMPLE = 3

HERE = os.path.dirname(os.path.abspath(__file__))
ICO_PATH = os.path.join(HERE, "icon.ico")
SVG_PATH = os.path.join(HERE, "static", "icon.svg")

# Palette, matching app.css.
SKY_TOP = (0x18, 0x0A, 0x2C)
SKY_BOTTOM = (0x4A, 0x12, 0x5E)
SUN_TOP = (0xFF, 0xE9, 0xA8)
SUN_MID = (0xFF, 0xB0, 0x38)
SUN_BOTTOM = (0xFF, 0x2E, 0x88)
GRID = (0x22, 0xE0, 0xFF)
HORIZON = (0xFF, 0x2E, 0x88)


def lerp(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def over(dst, src, alpha):
    """Composite src over dst at the given alpha."""
    return tuple(round(d + (s - d) * alpha) for d, s in zip(dst, src))


def rounded_rect_coverage(x, y, size, radius):
    """Signed coverage of a rounded square, used for the icon's silhouette."""
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    dist = math.hypot(x - cx, y - cy)
    return 1.0 if dist <= radius else 0.0


def shade(x, y, size):
    """Colour one sample point. Returns (r, g, b, a)."""
    if not rounded_rect_coverage(x, y, size, size * 0.22):
        return (0, 0, 0, 0)

    horizon = size * 0.60
    colour = lerp(SKY_TOP, SKY_BOTTOM, y / size)

    # ── grid below the horizon ────────────────────────────────────────────────
    if y > horizon:
        depth = (y - horizon) / (size - horizon)  # 0 at horizon, 1 at bottom
        line_w = size * 0.012

        # Horizontal rungs, bunched up near the horizon.
        for k in range(1, 9):
            ky = horizon + (size - horizon) * (k / 8.0) ** 1.9
            if abs(y - ky) < line_w:
                colour = over(colour, GRID, 0.75 * (1 - abs(y - ky) / line_w))

        # Verticals converging on the vanishing point.
        for i in range(-6, 7):
            if i == 0:
                continue
            vx = size * 0.5 + i * size * 0.16 * depth
            if abs(x - vx) < line_w:
                colour = over(colour, GRID, 0.65 * (1 - abs(x - vx) / line_w))

    # ── sun ───────────────────────────────────────────────────────────────────
    sun_cx, sun_cy, sun_r = size * 0.5, size * 0.46, size * 0.25
    dx, dy = x - sun_cx, y - sun_cy
    if dx * dx + dy * dy <= sun_r * sun_r:
        # Slice the lower half into bands.
        slice_h = size * 0.045
        sliced = dy > 0 and (int(dy / slice_h) % 2 == 1)
        if not sliced:
            t = (y - (sun_cy - sun_r)) / (2 * sun_r)
            sun = lerp(SUN_TOP, SUN_MID, min(1.0, t * 2)) if t < 0.5 \
                else lerp(SUN_MID, SUN_BOTTOM, (t - 0.5) * 2)
            colour = sun

    # ── horizon line ──────────────────────────────────────────────────────────
    if abs(y - horizon) < size * 0.012:
        colour = over(colour, HORIZON, 0.9)

    return (*colour, 255)


def render(size: int) -> bytes:
    """Render one size to raw RGBA bytes, supersampled for smooth edges."""
    ss = SUPERSAMPLE
    big = size * ss
    rows = bytearray()

    # Pre-render the supersampled grid one row-band at a time.
    for py in range(size):
        row = bytearray()
        for px in range(size):
            r = g = b = a = 0
            for sy in range(ss):
                for sx in range(ss):
                    sample = shade(
                        (px * ss + sx + 0.5) / ss,
                        (py * ss + sy + 0.5) / ss,
                        size,
                    )
                    r += sample[0] * sample[3]
                    g += sample[1] * sample[3]
                    b += sample[2] * sample[3]
                    a += sample[3]
            if a:
                row += bytes((r // a, g // a, b // a, a // (ss * ss)))
            else:
                row += b"\0\0\0\0"
        rows += b"\0" + row  # PNG filter byte 0 (None) per scanline
    return bytes(rows)


def png(size: int, raw: bytes) -> bytes:
    """Minimal RGBA PNG encoder."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def write_ico(path: str) -> None:
    images = []
    for size in SIZES:
        print(f"  rendering {size}x{size}...")
        images.append((size, png(size, render(size))))

    header = struct.pack("<HHH", 0, 1, len(images))
    offset = len(header) + 16 * len(images)

    entries = b""
    for size, data in images:
        entries += struct.pack(
            "<BBBBHHII",
            0 if size == 256 else size,  # 0 means 256 in the ICO format
            0 if size == 256 else size,
            0,  # palette size
            0,  # reserved
            1,  # colour planes
            32,  # bits per pixel
            len(data),
            offset,
        )
        offset += len(data)

    with open(path, "wb") as handle:
        handle.write(header + entries + b"".join(data for _, data in images))


SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">
  <defs>
    <linearGradient id="sky" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#180a2c"/><stop offset="1" stop-color="#4a125e"/>
    </linearGradient>
    <linearGradient id="sun" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffe9a8"/><stop offset=".45" stop-color="#ffb038"/>
      <stop offset="1" stop-color="#ff2e88"/>
    </linearGradient>
    <mask id="slice">
      <rect width="64" height="30" fill="#fff"/>
      <g fill="#fff">
        <rect y="30" width="64" height="2.4"/><rect y="35" width="64" height="2.4"/>
        <rect y="40" width="64" height="2.4"/>
      </g>
    </mask>
    <clipPath id="round"><rect width="64" height="64" rx="14"/></clipPath>
  </defs>
  <g clip-path="url(#round)">
    <rect width="64" height="64" fill="url(#sky)"/>
    <circle cx="32" cy="29.5" r="16" fill="url(#sun)" mask="url(#slice)"/>
    <g stroke="#22e0ff" stroke-width="1" opacity=".75">
      <path d="M0 44h64M0 48.5h64M0 54h64M0 61h64"/>
      <path d="M32 38.4 4 64M32 38.4 60 64M32 38.4 20 64M32 38.4 44 64"/>
    </g>
    <rect y="38" width="64" height="1.4" fill="#ff2e88"/>
  </g>
</svg>
"""


if __name__ == "__main__":
    print("Generating icon...")
    write_ico(ICO_PATH)
    os.makedirs(os.path.dirname(SVG_PATH), exist_ok=True)
    with open(SVG_PATH, "w", encoding="utf-8") as handle:
        handle.write(SVG)
    print(f"  wrote {ICO_PATH} ({os.path.getsize(ICO_PATH):,} bytes)")
    print(f"  wrote {SVG_PATH}")
