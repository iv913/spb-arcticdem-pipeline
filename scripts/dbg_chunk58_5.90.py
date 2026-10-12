#!/usr/bin/env python3
"""Диагностика слияния мозаики 5.90 на одном южном чанке (58)."""
import json
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.vrt import WarpedVRT
from rasterio.enums import Resampling

HOME = Path('/tmp/my-project/gh-clone')
PRODD = HOME / 'download/products_5.83'
PX = 0.0000178
NOD = -32768
QNOD = 65535

# ZONES из mosaic_5.90
import importlib.util
spec = importlib.util.spec_from_file_location(
    'm90', '/tmp/my-project/gh-clone/scripts/mosaic_5.90.py')
m90 = importlib.util.module_from_spec(spec)
sys.modules['m90'] = m90
spec.loader.exec_module(m90)          # выполняет верхний уровень (ZONES и пр.)

ZONES = m90.ZONES
W0, S0, E0, N0 = 29.36, 59.700018, 30.599983, 60.1183
GW, GH = 69662, 23500
gtr = rasterio.transform.from_origin(W0, N0, PX, PX)
CI = 58
r0, r1 = CI * 256, (CI + 1) * 256
h = r1 - r0
win = rasterio.windows.Window(0, r0, GW, h)
top = N0 - r0 * PX
bot = N0 - r1 * PX
print(f"чанк {CI}: top {top:.5f} bot {bot:.5f}")

tot = 0
for tag, b, _ in ZONES:
    if b[1] >= top or b[3] <= bot:
        continue
    p = PRODD / f"hybrid_2m_5.83_{m90.ftag(tag)}.tif"
    with rasterio.open(p) as src:
        v = WarpedVRT(src, crs='EPSG:4326', transform=gtr, width=GW,
                      height=GH, resampling=Resampling.nearest, nodata=NOD)
        dem = v.read(1, window=win)
        v.close()
    n = int((dem != NOD).sum())
    tot += n
    print(f"  {tag:6s} bbox {[round(x,4) for x in b]}: valid {n}")
print(f"сумма valid по зонам чанка: {tot}")
