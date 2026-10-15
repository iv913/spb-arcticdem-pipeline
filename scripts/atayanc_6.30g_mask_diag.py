#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30g — диагностика цветовой маски зданий."""
import numpy as np
from PIL import Image
from scipy import ndimage

im = np.asarray(Image.open('/home/z/my-project/upload/atayanc-2013-01.jpg').convert('RGB')).astype(np.int16)
H, W = im.shape[:2]

def patch(cx, cy, r=8):
    p = im[cy - r:cy + r, cx - r:cx + r].reshape(-1, 3)
    return np.median(p, 0).astype(int)

# известные здания (full-res): собор Вл., Петропавловский, Биржа, Зимний, кластер 23/28/32 (c1: render (500,320)->full (962,615)), обычный жилой (c2 render (900,120)->full (1730,231)?)
probes = {
    'собор Вл (salmon)': (858, 66),
    'Петропавловский (salmon)': (2363, 275),
    'Биржа (salmon)': (1512, 1224),
    'Зимний (salmon)': (2136, 1641),
    'тёмное 28/32 (c1 ~ 940,600)': (940, 600),
    'тёмное 28 (c1 ~ 870,470)': (870, 470),
    'жилой salmon (1600,300)': (1600, 300),
    'жилой salmon (1900,500)': (1900, 500),
    'вода (1600,700)': (1600, 700),
    'улица белая (1800,1450)': (1800, 1450),
}
for n, (x, y) in probes.items():
    print(f'{n:32s} {patch(x, y)}')

import colorsys
hsv = np.asarray(Image.open('/home/z/my-project/upload/atayanc-2013-01.jpg').convert('HSV')).astype(np.int16)
Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
print('\nHSV: hue 0-255; salmon hue ~', int(np.median(hsv[66 - 8:66 + 8, 858 - 8:858 + 8, 0])), 'sat', int(np.median(hsv[66 - 8:66 + 8, 858 - 8:858 + 8, 1])))
# маска по HSV: hue 5-35, sat>=28, val 100-250
m1 = (Hh >= 3) & (Hh <= 38) & (Ss >= 28) & (Vv >= 95) & (Vv <= 250)
m1 = ndimage.binary_closing(m1, np.ones((2, 2)))
lab, n = ndimage.label(m1)
sz = ndimage.sum(m1, lab, range(1, n + 1))
print(f'HSV-маска: покрытие {m1.mean()*100:.1f}%, компонент >=300px: {(sz >= 300).sum()}, >=100px: {(sz >= 100).sum()}, всего px {m1.sum()}')

rgb_salmon = (np.abs(im[..., 0] - 206) <= 34) & (np.abs(im[..., 1] - 170) <= 34) & (np.abs(im[..., 2] - 156) <= 34)
print(f'RGB-маска salmon: покрытие {rgb_salmon.mean()*100:.1f}%')

# дебаг-рендер HSV маски на 2 кропах
for name, (x0, y0, x1, y1) in [('dbg_c1', (700, 300, 1300, 800)), ('dbg_c4', (1950, 1400, 2500, 1900))]:
    rgb = im[y0:y1, x0:x1].astype(np.uint8).copy()
    mm = m1[y0:y1, x0:x1]
    rgb[mm & (rgb[..., 0] > 0)] = [255, 0, 0]
    rgb[~mm] = (rgb[~mm] * 0.55).astype(np.uint8)
    Image.fromarray(rgb).save(f'/tmp/my-project/results/atayanc_6.30/{name}.png')
print('dbg кропы записаны')
