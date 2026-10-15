#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50c2 — диагностика bias: робастные низкие перцентили окна fusion."""
import csv
import numpy as np
import rasterio

CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'

rows = list(csv.DictReader(open(CAT)))
res = []
with rasterio.open(FUSE) as fs:
    for r in rows:
        lon, lat = float(r['lon']), float(r['lat'])
        rr, c = fs.index(lon, lat)
        if not (0 <= rr < fs.height and 0 <= c < fs.width):
            continue
        w9 = fs.read(1, window=rasterio.windows.Window(c - 4, rr - 4, 9, 9))
        w9v = w9[w9 > -9000].astype(float) / 100.0
        w5 = fs.read(1, window=rasterio.windows.Window(c - 2, rr - 2, 5, 5))
        w5v = w5[w5 > -9000].astype(float) / 100.0
        if not len(w9v) or not len(w5v):
            continue
        v = float(r['h_bs77_m'])
        res.append((v, np.percentile(w9v, 10), np.percentile(w9v, 25),
                    np.percentile(w5v, 10), np.percentile(w5v, 25),
                    1.0 if r['fixed_650'] == '1' else 0.0, r['id']))

arr = np.array([x[:6] for x in res])
v, p10_9, p25_9, p10_5, p25_5, fx = arr.T
for name, ref in [('p10_9x9', p10_9), ('p25_9x9', p25_9), ('p10_5x5', p10_5),
                  ('p25_5x5', p25_5)]:
    d = v - ref
    cl = np.abs(d) <= 2.0
    print(f'{name:9s}: bias {d.mean():+.2f}, MAD {np.median(np.abs(d)):.2f}, '
          f'p95 {np.percentile(np.abs(d), 95):.2f}, |d|<=2: {cl.mean() * 100:.0f}%')
d = v - p10_5
for tag, m in [('fixed', fx > 0.5), ('keep', fx < 0.5)]:
    dd = d[m]
    print(f'p10_5x5 {tag:6s}: n={len(dd)}, bias {dd.mean():+.2f}, '
          f'MAD {np.median(np.abs(dd)):.2f}')
ids = [x[6] for x in res]
idx = np.argsort(d)[:12]
print('\nхудшие (v - p10_5x5):')
for i in idx:
    print(f'  {ids[i]}: v={v[i]:.2f} p10_5={p10_5[i]:.2f} p10_9={p10_9[i]:.2f} '
          f'fixed={int(fx[i])}')
