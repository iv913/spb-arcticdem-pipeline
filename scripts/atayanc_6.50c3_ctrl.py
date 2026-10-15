#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50c3 — контроль: fusion A2 на 50 отметках 6.30 (эталон MAD 0.98) + px худших."""
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
import json, csv

FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
MARKS = [
    (1188, 365, 2.39), (1360, 408, 2.62), (1297, 413, 2.43), (1307, 425, 2.68),
    (1348, 447, 2.49), (1427, 458, 2.48), (1380, 498, 2.16), (1410, 518, 2.75),
    (1403, 548, 2.52), (1400, 567, 2.04), (1442, 563, 2.97), (1517, 585, 2.70),
    (1413, 615, 4.00), (1297, 633, 4.64), (1378, 541, 3.75), (1438, 389, 2.46),
    (1523, 397, 2.51),
    (1468, 1025, 4.63), (1468, 1048, 3.86), (1523, 1041, 3.55), (1475, 1072, 3.55),
    (1635, 1067, 2.74), (1685, 1083, 2.74), (1632, 1103, 2.90), (1567, 1127, 3.72),
    (1533, 1130, 3.83), (1657, 1143, 2.89), (1640, 1170, 3.77), (1680, 1187, 3.96),
    (1627, 1245, 3.63), (1610, 1253, 3.83), (1680, 1253, 3.77), (1530, 1272, 4.50),
    (2330, 1381, 2.84), (2262, 1436, 2.80), (2387, 1433, 2.73), (2488, 1338, 3.00),
    (2522, 1425, 3.72), (2537, 1503, 2.83), (2445, 1518, 2.61), (2490, 1545, 3.04),
    (2378, 1641, 2.81),
    (2228, 1818, 2.85), (2367, 1827, 2.85), (2385, 1742, 2.86), (2152, 1887, 2.94),
    (2368, 1918, 2.97), (2458, 1780, 3.09), (2430, 1893, 2.82), (2165, 1862, 2.61),
]
geo = json.load(open('/home/z/my-project/download/atayanc_6.30/georef_6.30.json'))
A = RAff(*geo['affine_deg'])
with rasterio.open(FUSE) as fs:
    d = []
    for x, y, v in MARKS:
        px = x - 12
        lon = A.c + A.a * px + A.b * y
        lat = A.f + A.d * px + A.e * y
        r, c = fs.index(lon, lat)
        w = fs.read(1, window=rasterio.windows.Window(c - 2, r - 2, 5, 5))
        wv = w[w > -9000]
        fv = float(np.median(wv)) / 100.0 if len(wv) else np.nan
        d.append(v - fv)
    d = np.array(d)
    ok = ~np.isnan(d)
    print(f'50 отметок 6.30: n={int(ok.sum())}, bias {d[ok].mean():+.2f}, '
          f'MAD {np.median(np.abs(d[ok])):.2f}, p95 {np.percentile(np.abs(d[ok]), 95):.2f}')

# px худших новых отметок
rows = list(csv.DictReader(open('/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv')))
worst = {'m055', 'm377', 'm215', 'm178', 'm334', 'm054', 'm033', 'm034',
         'm032', 'm375', 'm373', 'm036', 'm122', 'm163', 'm165', 'm170'}
print('\nхудшие отметки (px, py, nw, lon, lat):')
for r in rows:
    if r['id'] in worst:
        print(f"  {r['id']}: px={r['px']} py={r['py']} nw={r['nw_fragment']} "
              f"lon={r['lon']} lat={r['lat']} v={r['h_bs77_m']} fusion={r['fusion_a2_m']}")
