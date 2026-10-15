#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30b — кропы плана Атаянс для чтения отметок и пиковки GCP."""
import os
from PIL import Image, ImageDraw

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
os.makedirs(OUTD, exist_ok=True)
im = Image.open(SRC).convert('RGB')
W, H = im.size
print('size', W, H)

# (имя, x0, y0, x1, y1) — по отрендеренному превью 1560x1119, масштаб x1.923
sx = W / 1560.0
Z = 2  # зум
crops = [
    ('c1_nevahaus',      380, 20,  760, 340),   # кластер с числами 26/28/32/38
    ('c2_cathedral',     330, 0,   620, 200),   # верх: собор / начало коридора
    ('c3_spit',          620, 250, 980, 560),   # стрелка, Биржа, круг
    ('c4_palace',        950, 700, 1360, 1119), # Зимний дворец, канавка
    ('c5_scale',         330, 1030, 780, 1119), # масштабная линейка
    ('c6_topright',      900, 0,   1300, 260),  # Кронверкский, петроградская
    ('c7_bridge_area',   620, 470, 950, 700),   # через Малую Неву, спуск коридора
    ('c8_makarova',      300, 200, 700, 480),   # наб. Макарова, север В.О.
]
for name, x0, y0, x1, y1 in crops:
    box = (int(x0 * sx), int(y0 * sx), int(x1 * sx), int(y1 * sx))
    c = im.crop(box)
    c = c.resize((c.width * Z, c.height * Z), Image.LANCZOS)
    d = ImageDraw.Draw(c)
    # сетка координат каждые 50 px (в системе полного изображения)
    for gx in range(box[0] // 50 * 50, box[2] + 1, 50):
        if box[0] <= gx <= box[2]:
            lx = (gx - box[0]) * Z
            d.line([(lx, 0), (lx, 12)], fill=(255, 0, 0), width=2)
            if gx % 100 == 0:
                d.text((lx + 2, 2), str(gx), fill=(255, 0, 0))
    for gy in range(box[1] // 50 * 50, box[3] + 1, 50):
        if box[1] <= gy <= box[3]:
            ly = (gy - box[1]) * Z
            d.line([(0, ly), (12, ly)], fill=(255, 0, 0), width=2)
            if gy % 100 == 0:
                d.text((14, ly + 1), str(gy), fill=(255, 0, 0))
    c.save(f'{OUTD}/{name}.png')
    print(name, box, c.size)
print('done ->', OUTD)
