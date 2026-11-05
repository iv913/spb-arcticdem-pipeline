#!/usr/bin/env python3
"""6.76/A3 — визуальная проверка топ-компонентов «сухих» пикселей.

Для каждого указанного компонента: окно 900x700 м, слои:
  фон — поверхность (серая шкала, 0..25 м), вода-полигон — синий контур,
  сухие пиксели (>=1.5 м, data) — красным, fill — оранжевым полупрозрачно.
PNG: results/water_a3_6.76/qa_comp_<i>_<feat>.png
"""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from PIL import Image
from shapely.geometry import shape, box

REPO = Path('/tmp/my-project/gh-clone')
W22 = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.22.geojson'
FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
OUTD = REPO / 'results' / 'water_a3_6.76'
OUTD.mkdir(parents=True, exist_ok=True)

CASES = [  # (label, lon, lat) — топ-компоненты из diag
    ('feat0_152ga', 30.4940, 59.9901),
    ('feat2_114ga', 30.2998, 60.0546),
    ('feat0_fortress', 30.2900, 59.9285),
    ('feat4_70ga', 30.1715, 59.8769),
]
HALF = 0.0045  # ~500x400 м

feats = json.load(open(W22))['features']
geoms = [shape(f['geometry']).buffer(0) for f in feats]
F = rasterio.open(FUS)
C = rasterio.open(CONF)

for i, (label, lon, lat) in enumerate(CASES):
    # фича, содержащая точку
    pt = box(lon - 1e-6, lat - 1e-6, lon + 1e-6, lat + 1e-6)
    fi = next((k for k, g in enumerate(geoms) if g.intersects(pt)), None)
    if fi is None:
        print(f'{label}: точка не в маске ({lon},{lat})')
        continue
    g = geoms[fi]
    win = from_bounds(lon - HALF, lat - HALF * 0.72, lon + HALF,
                      lat + HALF * 0.72, F.transform)
    win = win.round_offsets().round_lengths()
    x0i, y0i, w_, h_ = (int(win.col_off), int(win.row_off),
                        int(win.width), int(win.height))
    tr = F.window_transform(win)
    fu = F.read(1, window=Window(x0i, y0i, w_, h_)).astype(np.float32)
    cf = C.read(1, window=Window(x0i, y0i, w_, h_))
    wm = rasterize([(g, 1)], out_shape=(h_, w_), transform=tr, fill=0,
                   dtype='uint8')
    z = np.where(fu == -32768, np.nan, fu * 0.01)
    zn = np.clip((z - 0) / 25.0, 0, 1)
    rgb = (np.dstack([zn * 0.85 + 0.15] * 3) * 255).astype(np.uint8)
    water = wm == 1
    dry = water & (z >= 1.5) & (cf >= 10) & np.isfinite(z)
    dryfill = water & (z >= 1.5) & (cf >= 1) & (cf <= 4) & np.isfinite(z)
    rgb[dryfill] = (rgb[dryfill] * 0.5 + np.array([255, 165, 0]) * 0.5) \
        .astype(np.uint8)
    rgb[dry] = [255, 30, 30]
    # контур воды: пиксели воды рядом с не-водой
    from scipy import ndimage
    edge = water & ~ndimage.binary_erosion(water, iterations=1)
    rgb[edge] = [30, 80, 255]
    img = Image.fromarray(rgb).resize((w_ * 2, h_ * 2), Image.NEAREST)
    fp = OUTD / f'qa_comp_{i}_{label}.png'
    img.save(fp)
    print(f'{label}: feat{fi} {fp.name} {img.size}, dry px {int(dry.sum())}, '
          f'dryfill px {int(dryfill.sum())}')
