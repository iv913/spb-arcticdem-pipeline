#!/usr/bin/env python3
"""6.76/A3 — широкий QA-вид: маска 6.22 (контур+заливка) vs поверхность.
Виды: восток Невы (30.49,59.99), Лесная фантом (30.30,60.055), крепость."""
import json
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize
from rasterio.windows import Window, from_bounds
from PIL import Image
from shapely.geometry import shape, box
from scipy import ndimage

REPO = Path('/tmp/my-project/gh-clone')
W22 = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.76.geojson'
FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
OUTD = REPO / 'results' / 'water_a3_6.76'

CASES = [
    ('wide_neva_east', 30.494, 59.990, 0.012),
    ('wide_lesnoy', 30.300, 60.056, 0.012),
    ('wide_fortress', 30.295, 59.930, 0.010),
]
feats = json.load(open(W22))['features']
geoms = [shape(f['geometry']).buffer(0) for f in feats]
F = rasterio.open(FUS)
C = rasterio.open(CONF)

for label, lon, lat, half in CASES:
    win = from_bounds(lon - half, lat - half * 0.66, lon + half,
                      lat + half * 0.66, F.transform)
    win = win.round_offsets().round_lengths()
    x0i, y0i, w_, h_ = int(win.col_off), int(win.row_off), int(win.width), \
        int(win.height)
    tr = F.window_transform(win)
    fu = F.read(1, window=Window(x0i, y0i, w_, h_)).astype(np.float32)
    cf = C.read(1, window=Window(x0i, y0i, w_, h_))
    z = np.where(fu == -32768, np.nan, fu * 0.01)
    zn = np.clip(z / 20.0, 0, 1)
    rgb = (np.dstack([zn * 0.8 + 0.12] * 3) * 255).astype(np.uint8)
    # маска: все фичи, пересекающие окно
    tb = box(lon - half, lat - half, lon + half, lat + half)
    wm = np.zeros((h_, w_), bool)
    for g in geoms:
        if g.intersects(tb):
            wm |= rasterize([(g, 1)], out_shape=(h_, w_), transform=tr,
                            fill=0, dtype='uint8') == 1
    dry = wm & (z >= 1.5) & (cf >= 10) & np.isfinite(z)
    edge = wm & ~ndimage.binary_erosion(wm, iterations=1)
    rgb[edge] = [40, 90, 255]
    rgb[dry] = [255, 40, 40]
    Image.fromarray(rgb).resize((w_, h_), Image.NEAREST).save(
        OUTD / f'qa76_{label}.png')
    print(label, 'saved', (w_, h_), 'dry px', int(dry.sum()))
