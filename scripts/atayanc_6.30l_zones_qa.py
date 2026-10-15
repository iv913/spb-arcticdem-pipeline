#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30l — QA геопривязки по зонам: план (гео) vs OSM-здания."""
import json
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import rasterio
from rasterio.transform import rowcol

OUTD = '/tmp/my-project/results/atayanc_6.30'
with rasterio.open(f'{OUTD}/atayanc_plan_georef_6.30.tif') as s:
    pass  # достаточно transform из json
geo = json.load(open(f'{OUTD}/georef_6.30.json'))
a_, b_, c_, d_, e_, f_ = geo['affine_deg']

img = plt.imread('/home/z/my-project/upload/atayanc-2013-01.jpg')
bjson = json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))

def to_px(lon, lat):
    # обратная аффинная 2x2
    M = np.array([[a_, b_], [d_, e_]])
    x, y = np.linalg.solve(M, np.array([lon - c_, lat - f_]))
    return x, y

zones = {
    'NW (стадион/Ждановка)': (0, 0, 800, 500),
    'N (зоопарк?/Кронверкский)': (1200, 0, 2100, 500),
    'NE (крепость)': (2000, 0, 3000, 600),
    'C (стрелка)': (1100, 700, 2100, 1400),
    'SE (Дворцовая)': (1900, 1400, 2900, 2121),
}
fig, axs = plt.subplots(len(zones), 1, figsize=(15, 5.2 * len(zones)), constrained_layout=True)
for ax, (zn, box) in zip(axs, zones.items()):
    x0, y0, x1, y1 = box
    ax.imshow(img[y0:y1, x0:x1])
    # OSM здания в этой зоне
    cnt = 0
    for ft in bjson['features']:
        g = np.array(ft['geometry']['coordinates'][0], float)
        u, v = to_px(g[:, 0].mean(), g[:, 1].mean())
        if x0 - 100 < u < x1 + 100 and y0 - 100 < v < y1 + 100:
            px, py = to_px(g[:, 0], g[:, 1])
            ax.plot(px - x0, py - y0, color='lime', lw=1.0, alpha=0.85)
            cnt += 1
    ax.set_title(f'{zn}: план, зелёным OSM-контуры ({cnt})', fontsize=11)
    ax.grid(color='red', alpha=0.25, lw=0.4)
    ax.set_xticks(range(0, x1 - x0, 100)); ax.set_yticks(range(0, y1 - y0, 100))
    ax.tick_params(labelsize=6)
fig.savefig(f'{OUTD}/qa_zones.png', dpi=110)
print('qa_zones.png готов')
