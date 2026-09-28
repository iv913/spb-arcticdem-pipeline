#!/usr/bin/env python3
"""Хелпер: вырезать фрагмент изображения с сеткой координат.

usage: crop_grid.py <img> <x0> <y0> <w> <h> <out> [step]
Рисует сетку с подписями абсолютных пиксельных координат.
"""
import sys

from PIL import Image, ImageDraw

src, x0, y0, w, h, out = sys.argv[1], *map(int, sys.argv[2:6]), sys.argv[6]
step = int(sys.argv[7]) if len(sys.argv) > 7 else 50
im = Image.open(src).convert("RGB")
crop = im.crop((x0, y0, x0 + w, y0 + h))
# масштаб x2 для читаемости
crop = crop.resize((w * 2, h * 2), Image.LANCZOS)
d = ImageDraw.Draw(crop)
for gx in range(0, w, step):
    d.line([(gx * 2, 0), (gx * 2, h * 2)], fill=(0, 200, 255), width=1)
    d.text((gx * 2 + 2, 2), str(x0 + gx), fill=(255, 0, 0))
for gy in range(0, h, step):
    d.line([(0, gy * 2), (w * 2, gy * 2)], fill=(0, 200, 255), width=1)
    d.text((2, gy * 2 + 2), str(y0 + gy), fill=(255, 0, 0))
crop.save(out)
print(f"saved {out}: pixel origin ({x0},{y0}), size {w}x{h}, step {step}")
