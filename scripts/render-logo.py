#!/usr/bin/env python3
"""Растеризует единый знак OmniSecretary в PNG для аватара бота.

Источник — те же геометрические формы, что в theme/omni-logo.svg и
Android ic_launcher_foreground.xml. Вне зависимости меняется только размер.
"""

from pathlib import Path
import struct
import zlib

SIZE = 512
SS = 3  # суперсэмплинг для сглаживания

BG = (60, 56, 54, 255)        # #3c3836
YELLOW = (250, 189, 47, 255)  # #fabd2f
DARK = (40, 40, 40, 255)      # #282828


def rounded_rect(x: float, y: float, w: float, h: float, r: float):
    def inside(px: float, py: float) -> bool:
        if x <= px <= x + w and y <= py <= y + h:
            cx = min(max(px, x + r), x + w - r)
            cy = min(max(py, y + r), y + h - r)
            return (px - cx) ** 2 + (py - cy) ** 2 <= r * r
        return False
    return inside


def circle(cx: float, cy: float, r: float):
    return lambda px, py: (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def triangle(points):
    (ax, ay), (bx, by), (cx, cy) = points

    def sign(p1, p2, p3):
        return (p1[0] - p3[0]) * (p2[1] - p3[1]) - (p2[0] - p3[0]) * (p1[1] - p3[1])

    def inside(px, py):
        p = (px, py)
        d1, d2, d3 = sign(p, (ax, ay), (bx, by)), sign(p, (bx, by), (cx, cy)), sign(p, (cx, cy), (ax, ay))
        has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
        has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
        return not (has_neg and has_pos)
    return inside


def any_of(*shapes):
    return lambda px, py: any(shape(px, py) for shape in shapes)


def main() -> None:
    scale = SIZE / 64.0
    outer = rounded_rect(3, 3, 58, 58, 17)
    border_inner = rounded_rect(5, 5, 54, 54, 15)
    bubble = any_of(
        rounded_rect(17, 20, 34, 25, 4),
        triangle(((23, 44), (33, 44), (23, 52))),
    )
    dots = [circle(x, 32, 2.5) for x in (25, 32, 39)]

    rows = []
    for py in range(SIZE):
        row = bytearray()
        for px in range(SIZE):
            acc = [0, 0, 0, 0]
            for sy in range(SS):
                for sx in range(SS):
                    x = (px + (sx + 0.5) / SS) / scale
                    y = (py + (sy + 0.5) / SS) / scale
                    if not outer(x, y):
                        color = (0, 0, 0, 0)
                    elif bubble(x, y):
                        color = YELLOW
                    elif any(dot(x, y) for dot in dots):
                        color = DARK
                    elif border_inner(x, y):
                        color = BG
                    else:
                        color = YELLOW
                    for i in range(4):
                        acc[i] += color[i]
            n = SS * SS
            row.extend(bytes(v // n for v in acc))
        rows.append(bytes(row))

    raw = b"".join(b"\x00" + row for row in rows)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 6, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))

    out = Path(__file__).resolve().parent.parent / "bot" / "avatar.png"
    out.write_bytes(png)
    print(f"Записан {out} ({SIZE}x{SIZE}, {len(png)} байт)")


if __name__ == "__main__":
    main()
