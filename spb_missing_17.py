#!/usr/bin/env python3
"""
spb_missing_17.py — разобрать 17 недостающих паспортных точек.

n_total=202, n_valid=185, n_missing=17. Точки внутри растра (202/202),
но h_model=NaN — NN search не нашёл finite pixel в радиусе 30 px.

Гипотеза: это точки в water_mask (detail=32, surface=0) или на границе.
"""
import os, sys, json, math, re
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.windows import Window
from rasterio.warp import transform_bounds
from rasterio.transform import from_origin
import pyproj

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
PERSIST = HOME / 'upload' / 'scripts_persistent'
PASSPORT_TXT = HOME / 'upload' / 'Отметки.txt'
TIF = OUT / 'spb_ground_surface_2m.tif'

SHIFT_M = 15.673
SPB_BBOX = (29.70, 59.75, 30.55, 60.05)

# Load passport
lines = PASSPORT_TXT.read_text(encoding='utf-8').strip().split('\n')
lats, lons, hps = [], [], []
for line in lines:
    line = line.strip()
    if not line: continue
    m = re.match(r'(\d+\.?\d*)\s*,\s*(\d+\.?\d*)\s*:\s*(\d+)[,\.](\d*)', line)
    if m:
        lats.append(float(m.group(1))); lons.append(float(m.group(2)))
        hps.append(float(m.group(3) + '.' + (m.group(4) or '0')))
pp = pd.DataFrame({'lat': lats, 'lon': lons, 'hp': hps})
print(f'Passport: {len(pp)} points')

# Transform to EPSG:3413
spb_3413 = transform_bounds('EPSG:4326', 'EPSG:3413', *SPB_BBOX)
left, bottom, right, top = spb_3413
left = math.floor(left / 2) * 2; bottom = math.floor(bottom / 2) * 2
right = math.ceil(right / 2) * 2; top = math.ceil(top / 2) * 2
width = int((right - left) / 2); height = int((top - bottom) / 2)
transform = from_origin(left, top, 2, 2)
shape = (height, width)
inv = ~transform

tr_fwd = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
pp['x'] = pp.apply(lambda r: tr_fwd.transform(r.lon, r.lat)[0], axis=1)
pp['y'] = pp.apply(lambda r: tr_fwd.transform(r.lon, r.lat)[1], axis=1)
pp['row'] = pp.apply(lambda r: int(round((inv * (r.x, r.y))[1])), axis=1)
pp['col'] = pp.apply(lambda r: int(round((inv * (r.x, r.y))[0])), axis=1)

# Check inside
pp['inside'] = (pp.row >= 0) & (pp.row < shape[0]) & (pp.col >= 0) & (pp.col < shape[1])
print(f'Inside raster: {pp.inside.sum()}/{len(pp)}')

# For each point, read surface+detail at exact pixel AND with bigger NN radius
missing_results = []
with rasterio.open(TIF) as src:
    nodata = src.nodata
    for i, p in pp.iterrows():
        r, c = int(p.row), int(p.col)
        if not (0 <= r < shape[0] and 0 <= c < shape[1]):
            missing_results.append({
                'lat': p.lat, 'lon': p.lon, 'hp': p.hp,
                'status': 'outside_raster', 'detail_at': None, 'h_at': None,
                'nan_in_30px': None, 'nan_in_100px': None, 'water_in_100px': None,
            })
            continue
        
        # Read 1x1 at point
        d_block = src.read(2, window=Window(c, r, 1, 1)).astype('float32').astype('int')
        h_block = src.read(1, window=Window(c, r, 1, 1)).astype('float32')
        detail_at = int(d_block[0, 0])
        h_at = float(h_block[0, 0])
        h_at = None if h_at == nodata else h_at
        
        # Read 100x100 window for stats
        R = 50
        r0 = max(0, r-R); r1 = min(shape[0], r+R+1)
        c0 = max(0, c-R); c1 = min(shape[1], c+R+1)
        win_block = src.read(1, window=Window(c0, r0, c1-c0, r1-r0)).astype('float32')
        detail_block = src.read(2, window=Window(c0, r0, c1-c0, r1-r0)).astype('float32').astype('uint8')
        win_block[win_block == nodata] = np.nan
        
        n_nan_30 = int(np.isnan(win_block[r-r0-15:r-r0+16, c-c0-15:c-c0+16]).sum()) if 15 <= r-r0 <= win_block.shape[0]-16 and 15 <= c-c0 <= win_block.shape[1]-16 else None
        n_nan_100 = int(np.isnan(win_block).sum())
        n_water_100 = int((detail_block == 32).sum())
        n_total = win_block.size
        pct_water = round(100 * n_water_100 / n_total, 1) if n_total > 0 else 0
        pct_nan = round(100 * n_nan_100 / n_total, 1) if n_total > 0 else 0
        
        # NN search at larger radius (100 px = 200m)
        h_nn = None
        nn_dist_px = None
        if h_at is None or not np.isfinite(h_at) or h_at == 0:
            for rad in range(1, 100):
                found = False
                for dr in range(-rad, rad+1):
                    for dc in range(-rad, rad+1):
                        if dr*dr + dc*dc > rad*rad: continue
                        nr, nc = (r-r0) + dr, (c-c0) + dc
                        if 0 <= nr < win_block.shape[0] and 0 <= nc < win_block.shape[1]:
                            v = win_block[nr, nc]
                            if np.isfinite(v) and v > 0:  # exclude water (h=0)
                                h_nn = float(v)
                                nn_dist_px = rad
                                found = True; break
                    if found: break
                if found: break
        
        status = 'ok' if (h_at is not None and np.isfinite(h_at) and h_at > 0) else 'missing'
        missing_results.append({
            'lat': p.lat, 'lon': p.lon, 'hp': p.hp,
            'status': status,
            'detail_at': detail_at,
            'h_at': round(h_at, 2) if h_at is not None and np.isfinite(h_at) else None,
            'h_nn_200m': round(h_nn, 2) if h_nn is not None else None,
            'nn_dist_px': nn_dist_px,
            'pct_water_100px': pct_water,
            'pct_nan_100px': pct_nan,
        })

df = pd.DataFrame(missing_results)
missing = df[df.status == 'missing']
print(f'\n=== {len(missing)} MISSING POINTS ===')
print(f'{"lat":>10} {"lon":>10} {"hp":>6} {"detail":>7} {"h_at":>6} {"h_nn":>6} {"nn_px":>5} {"%water":>7} {"%nan":>5}')
print('-' * 80)
for _, r in missing.iterrows():
    print(f'{r.lat:>10.5f} {r.lon:>10.5f} {r.hp:>6.1f} {r.detail_at:>7} '
          f'{(str(r.h_at) if r.h_at is not None else "None"):>6} '
          f'{(str(r.h_nn_200m) if r.h_nn_200m is not None else "None"):>6} '
          f'{(str(r.nn_dist_px) if r.nn_dist_px is not None else "-"):>5} '
          f'{r.pct_water_100px:>7.1f} {r.pct_nan_100px:>5.1f}')

print(f'\n=== Distribution of detail_at for missing ===')
print(missing.detail_at.value_counts())

print(f'\n=== Distribution of detail_at for ALL points ===')
print(df.detail_at.value_counts())

print(f'\n=== Stats for missing ===')
print(f'  Mean % water in 100px window: {missing.pct_water_100px.mean():.1f}%')
print(f'  Mean % NaN in 100px window:   {missing.pct_nan_100px.mean():.1f}%')
print(f'  Found via NN search (200m): {missing.h_nn_200m.notna().sum()}/{len(missing)}')

# Save CSV
df.to_csv(str(OUT / 'spb_missing_17_analysis.csv'), index=False)
df.to_csv(str(PERSIST / 'spb_missing_17_analysis.csv'), index=False)
print(f'\n✓ Saved: spb_missing_17_analysis.csv')
