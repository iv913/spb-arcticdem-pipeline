#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50c4 — hillshade fusion A2 + отметки каталога 6.50: куда попадают точки."""
import csv
import numpy as np
import rasterio
import matplotlib.font_manager as fm
for fp in ['/usr/share/fonts/truetype/chinese/NotoSansSC-Regular.ttf',
           '/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf']:
    try:
        fm.fontManager.addfont(fp)
    except Exception:
        pass
import matplotlib.pyplot as plt
from matplotlib.colors import TwoSlopeNorm

plt.rcParams['font.sans-serif'] = ['Noto Sans SC', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'

with rasterio.open(FUSE) as fs:
    x0, y0, x1, y1 = 30.283, 59.9355, 30.332, 59.9535
    r0, c0 = fs.index(x0, y1)
    r1, c1 = fs.index(x1, y0)
    win = rasterio.windows.Window(c0, r0, c1 - c0, r1 - r0)
    z = fs.read(1, window=win).astype(float) / 100.0
    z[z < -50] = np.nan
    tr = fs.window_transform(win)
    # hillshade
    gy, gx = np.gradient(z, abs(tr.e), tr.a)
    az, alt = 315.0, 45.0
    slope = np.pi / 2 - np.arctan(np.sqrt(gx ** 2 + gy ** 2))
    aspect = np.arctan2(-gx, gy)
    hs = np.sin(np.radians(alt)) * slope + np.cos(np.radians(alt)) * np.cos(aspect - np.radians(az)) * np.cos(slope)
    hs = np.clip(hs, 0, 1)

rows = list(csv.DictReader(open(CAT)))
lons = [float(r['lon']) for r in rows]
lats = [float(r['lat']) for r in rows]
diff = [float(r['diff_m']) if r['diff_m'] else np.nan for r in rows]
fixed = [int(r['fixed_650']) for r in rows]

fig, axes = plt.subplots(1, 2, figsize=(19, 8.6), constrained_layout=True)
for ax, mode in zip(axes, ['fusion', 'diff']):
    if mode == 'fusion':
        ax.imshow(hs, cmap='gray', origin='upper',
                  extent=[x0, x1, y0, y1], aspect='auto')
        sc = ax.scatter(lons, lats, c=diff, cmap='coolwarm', s=14,
                        vmin=-8, vmax=8, edgecolors='k', linewidths=0.25)
        cb = fig.colorbar(sc, ax=ax, shrink=0.85)
        cb.set_label('отметка − fusion, м')
        ax.set_title('Hillshade fusion A2 + 332 отметки (цвет = разница)')
    else:
        ax.imshow(hs, cmap='gray', origin='upper',
                  extent=[x0, x1, y0, y1], aspect='auto')
        col = ['#2b8a3e' if f else '#c22525' for f in fixed]
        ax.scatter(lons, lats, c=col, s=14, edgecolors='k', linewidths=0.25)
        ax.set_title('зелёные = исправленные (117), красные = как было')
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_xlabel('долгота')
    ax.set_ylabel('широта')
    ax.grid(alpha=0.25, lw=0.4)
fig.savefig('/home/z/my-project/download/atayanc_6.50/diag_marks_vs_fusion_6.50.png', dpi=150)
print('saved diag png')
