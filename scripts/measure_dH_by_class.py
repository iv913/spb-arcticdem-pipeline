#!/usr/bin/env python3
"""Повторный замер dH по паспортным точкам, разложенный по классам пикселя (detail band)."""
import json, sys
import numpy as np, rasterio
from rasterio.warp import transform as warp_transform
from pathlib import Path
sys.path.insert(0, '/home/z/my-project/scripts')
import importlib.util as _ilu
_spec = _ilu.spec_from_file_location('m01', '/home/z/my-project/scripts/01_owner_map.py')
m01 = _ilu.module_from_spec(_spec); _spec.loader.exec_module(m01)
_spec2 = _ilu.spec_from_file_location('libg', '/home/z/my-project/scripts/lib_ground.py')
libg = _ilu.module_from_spec(_spec2); _spec2.loader.exec_module(libg)

OUT = m01.OUT
H_TIF = OUT / 'ground_surface.tif'

cp = m01.load_control_points()
print(f'Точек: {len(cp)}')

with rasterio.open(str(H_TIF)) as src:
    h = src.read(1); detail = src.read(3)
    transform, crs = src.transform, src.crs
    if src.nodata is not None: h[h == src.nodata] = np.nan

xs, ys = warp_transform('EPSG:4326', crs, cp['lon'].tolist(), cp['lat'].tolist())
inv = ~transform

results = []
for (_, p), x, y in zip(cp.iterrows(), xs, ys):
    c0, r0 = inv * (x, y)
    r0, c0 = int(np.floor(r0)), int(np.floor(c0))
    if not (0 <= r0 < h.shape[0] and 0 <= c0 < h.shape[1]): continue
    rc = libg.nearest_finite(h, r0, c0, radii=(0, 2, 5, 8))
    if rc is None: continue
    r, c = rc
    hv = float(h[r, c]); det = int(detail[r, c])
    if not np.isfinite(hv): continue
    dH = hv - p['h_pas']
    results.append({'lat': p['lat'], 'lon': p['lon'], 'hp': p['h_pas'],
                    'hs': hv, 'dH': dH, 'detail': det})

print(f'Точек на поверхности: {len(results)}\n')

CLASS_NAMES = {11:'street_meas (11)', 12:'street_rec (12)', 13:'street_ext (13)',
               14:'street_bridged (14)', 21:'open_accepted (21)',
               22:'open_near_anchor (22)', 23:'open_far_anchor (23)',
               31:'edge (31)', 32:'water_body (32) ⚠ акватория',
               41:'no_anchor (41)', 42:'reclaimed_stale_dem (42) — намыв'}

from collections import defaultdict
by_class = defaultdict(list)
for r in results: by_class[r['detail']].append(r)

print(f'{"Класс":<48} {"n":>3} {"медиана dH":>12} {"MAD":>8}')
print('-'*78)
land_results = []; water_results = []; reclaim_results = []
for cls in sorted(by_class.keys()):
    rs = by_class[cls]
    dHs = np.array([r['dH'] for r in rs])
    med = float(np.median(dHs))
    mad = float(np.median(np.abs(dHs - med))) * 1.4826 if len(dHs) > 1 else 0.0
    name = CLASS_NAMES.get(cls, f'unknown ({cls})')
    print(f'{name:<48} {len(rs):>3} {med:>+10.2f}м {mad:>6.2f}м')
    if cls == 32: water_results.extend(rs)
    elif cls == 42: reclaim_results.extend(rs)
    else: land_results.extend(rs)
print('-'*78)
print(f'{"ВСЕГО":<48} {len(results):>3}')

dHs_land = np.array([r['dH'] for r in land_results])
med_l = float(np.median(dHs_land)); mad_l = float(np.median(np.abs(dHs_land - med_l))) * 1.4826
print(f'\nБез точек на воде/намыве: {len(land_results)} точек')
print(f'  медиана dH: {med_l:+.2f}м, MAD: {mad_l:.2f}м')

filt = [r for r in land_results if r['hs'] <= 5.0]
if filt:
    dHs_f = np.array([r['dH'] for r in filt])
    med_f = float(np.median(dHs_f)); mad_f = float(np.median(np.abs(dHs_f - med_f))) * 1.4826
    print(f'\nПосле отсечения hs>5м (крыши): {len(filt)} точек')
    print(f'  медиана dH: {med_f:+.2f}м, MAD: {mad_f:.2f}м')

if water_results:
    print(f'\n=== Точки на воде ({len(water_results)}) — отдельно ===')
    for r in water_results:
        print(f'  ({r["lat"]:.5f}, {r["lon"]:.5f}) hp={r["hp"]:.2f} hs={r["hs"]:.2f} dH={r["dH"]:+.2f}м')

if reclaim_results:
    print(f'\n=== Точки на намыве ({len(reclaim_results)}) — интерполяция от соседей ===')
    for r in reclaim_results:
        print(f'  ({r["lat"]:.5f}, {r["lon"]:.5f}) hp={r["hp"]:.2f} hs={r["hs"]:.2f} dH={r["dH"]:+.2f}м')

import pandas as pd
pd.DataFrame(results).to_csv(OUT / 'passport_dH_by_class_v6_4.csv', index=False)
print(f'\n✓ {OUT/"passport_dH_by_class_v6_4.csv"}')
