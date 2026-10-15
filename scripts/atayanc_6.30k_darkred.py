#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30k — тёмно-красные (проектные) здания: полигоны + контактные листы для чтения высот.
Также: грид-кропы плана для визуального чтения отметок земли."""
import json
import numpy as np
from PIL import Image, ImageDraw
from scipy import ndimage
import rasterio
from rasterio.features import shapes as rio_shapes
from rasterio.transform import Affine as RAff

SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
OUTD = '/tmp/my-project/results/atayanc_6.30'
geo = json.load(open(f'{OUTD}/georef_6.30.json'))
a_, b_, c_, d_, e_, f_ = geo['affine_deg']
AFF = RAff(a_, b_, c_, d_, e_, f_)
img = Image.open(SRC).convert('RGB')
im = np.asarray(img).astype(np.int16)
hsv = np.asarray(img.convert('HSV')).astype(np.int16)
Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
H, W = im.shape[:2]

# тёмно-красный: R 135-205, G 55-125, B 25-90, R-G>=40
darkred = (im[..., 0] >= 130) & (im[..., 0] <= 215) & (im[..., 1] >= 45) & (im[..., 1] <= 130) & \
          (im[..., 2] >= 20) & (im[..., 2] <= 100) & ((im[..., 0] - im[..., 1]) >= 40)
darkred = ndimage.binary_opening(darkred, np.ones((2, 2)))
darkred = ndimage.binary_closing(darkred, np.ones((3, 3)))
lab, n = ndimage.label(darkred)
sizes = ndimage.sum(darkred, lab, range(1, n + 1))
ids = [i + 1 for i in range(n) if sizes[i] >= 900]  # >= ~520 м2
print(f'тёмно-красных полигонов >=900 px: {len(ids)}')

t = AFF  # транслируем пиксельные центроиды в координаты
feats = []
crops = []
for k, i in enumerate(ids):
    m = lab == i
    cy, cx = ndimage.center_of_mass(m)
    ys, xs = np.where(m)
    x0, x1, y0, y1 = xs.min(), xs.max(), ys.min(), ys.max()
    lon = AFF.c + AFF.a * cx + AFF.b * cy
    lat = AFF.f + AFF.d * cx + AFF.e * cy
    geoms = rio_shapes(m.astype(np.uint8), mask=m, transform=AFF)
    best, ba = None, 0
    for g, v in geoms:
        from shapely.geometry import shape
        ar = shape(g).area
        if ar > ba:
            best, ba = g, ar
    feats.append({'id': k, 'px': [int(cx), int(cy)], 'area_px': int(sizes[i - 1]),
                  'lonlat': [round(lon, 6), round(lat, 6)], 'geom': best})
    crops.append((k, x0, y0, x1, y1))
json.dump(feats, open(f'{OUTD}/darkred_poly_6.30.json', 'w'), ensure_ascii=False)

# контактные листы (по 9 на лист, зум х2.5)
cols, cw, ch = 3, 330, 300
persheet = cols * 3
for si in range(0, len(crops), persheet):
    chunk = crops[si:si + persheet]
    sh = Image.new('RGB', (cols * cw, ((len(chunk) + cols - 1) // cols) * ch), (245, 245, 245))
    d = ImageDraw.Draw(sh)
    for j, (k, x0, y0, x1, y1) in enumerate(chunk):
        c = img.crop((max(0, x0 - 12), max(0, y0 - 12), min(W, x1 + 12), min(H, y1 + 12)))
        z = min(2.5, (cw - 20) / c.width, (ch - 40) / c.height)
        c = c.resize((int(c.width * z), int(c.height * z)), Image.LANCZOS)
        px, py = (j % cols) * cw, (j // cols) * ch
        sh.paste(c, (px + 8, py + 30))
        d.text((px + 8, py + 6), f'ID {k}', fill=(200, 0, 0))
    sh.save(f'{OUTD}/sheet_darkred_{si // persheet}.png')
    print(f'лист {si // persheet}: {len(chunk)} полигонов')

# грид-кропы для отметок: 6x5 = 30 тайлов 500x355 @2x -> слипами по 4 на лист (8 листов)
tw, th = 500, 355
tile = []
for ty in range(5):
    for tx in range(6):
        x0, y0 = tx * tw, ty * th
        tile.append((x0, y0, min(W, x0 + tw), min(H, y0 + th)))
per = 4
for si in range(0, len(tile), per):
    sh = Image.new('RGB', (2 * (tw * 2 + 30), 2 * (th * 2 + 30)), (245, 245, 245))
    d = ImageDraw.Draw(sh)
    for j, (x0, y0, x1, y1) in enumerate(tile[si:si + per]):
        c = img.crop((x0, y0, x1, y1)).resize((tw * 2, th * 2), Image.LANCZOS)
        cd = ImageDraw.Draw(c)
        for gx in range(x0 // 50 * 50, x1 + 1, 50):
            if x0 <= gx <= x1:
                lx = (gx - x0) * 2
                cd.line([(lx, 0), (lx, 14 if gx % 100 == 0 else 7)], fill=(255, 0, 0), width=1)
                if gx % 100 == 0:
                    cd.text((lx + 2, 14), str(gx), fill=(255, 0, 0))
        for gy in range(y0 // 50 * 50, y1 + 1, 50):
            if y0 <= gy <= y1:
                ly = (gy - y0) * 2
                cd.line([(0, ly), (14 if gy % 100 == 0 else 7, ly)], fill=(255, 0, 0), width=1)
                if gy % 100 == 0:
                    cd.text((16, ly + 1), str(gy), fill=(255, 0, 0))
        px, py = (j % 2) * (tw * 2 + 30), (j // 2) * (th * 2 + 30)
        sh.paste(c, (px, py))
        d.text((px + 4, py + 2), f'T{si + j}', fill=(0, 0, 200))
    sh.save(f'{OUTD}/marks_grid_{si // per}.png')
print('грид-кропы готовы:', len(tile) // per, 'листов')
