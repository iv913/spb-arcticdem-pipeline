#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30d — уточняющие кропы для пиковки GCP (стрелка, Кунсткамера, Дворцовая пл., мост)."""
import os
from PIL import Image, ImageDraw

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
im = Image.open(SRC).convert('RGB')
Z = 3
crops = [
    ('g1_spit',      1300,  880, 1780, 1260),  # стрелка: Биржа, 2 колонны, полукруг
    ('g2_bridge',    1360,  620, 1560, 1080),  # Биржевой мост целиком
    ('g3_kunst',     1360, 1380, 1840, 1800),  # Кунсткамера / Университетская наб.
    ('g4_palsq',     2140, 1700, 2470, 2000),  # Александровская колонна
]
for name, x0, y0, x1, y1 in crops:
    c = im.crop((x0, y0, x1, y1))
    c = c.resize((c.width * Z, c.height * Z), Image.LANCZOS)
    d = ImageDraw.Draw(c)
    for gx in range(x0 // 20 * 20, x1 + 1, 20):
        if x0 <= gx <= x1:
            lx = (gx - x0) * Z
            d.line([(lx, 0), (lx, 10 if gx % 100 else 20)], fill=(255, 0, 0), width=2)
            if gx % 100 == 0:
                d.text((lx + 2, 20), str(gx), fill=(255, 0, 0))
    for gy in range(y0 // 20 * 20, y1 + 1, 20):
        if y0 <= gy <= y1:
            ly = (gy - y0) * Z
            d.line([(0, ly), (10 if gy % 100 else 20, ly)], fill=(255, 0, 0), width=2)
            if gy % 100 == 0:
                d.text((22, ly + 1), str(gy), fill=(255, 0, 0))
    c.save(f'{OUTD}/{name}.png')
    print(name, (x0, y0, x1, y1), c.size)
