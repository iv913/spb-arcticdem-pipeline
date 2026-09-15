#!/usr/bin/env python3
"""
spb_missing_17_no_tiff.py — анализ 17 недостающих точек БЕЗ tiff.

Использует OSM water polygons + Copernicus < 0.5 м для определения,
попала ли точка в воду.
"""
import os, sys, json, math, re
from pathlib import Path
import numpy as np
import pandas as pd
import rasterio
from rasterio.warp import transform_bounds
from rasterio.transform import from_origin
import shapely.geometry as sg
import shapely.ops as so
import pyproj

HOME = Path('/home/z/my-project')
OUT = HOME / 'download'
OUT.mkdir(parents=True, exist_ok=True)
PERSIST = HOME / 'upload' / 'scripts_persistent'
PASSPORT_TXT = HOME / 'upload' / 'Отметки.txt'
WATER_GJSON = OUT / 'spb_osm_water.geojson'
COP_TIF = HOME / 'scripts' / 'dem_cache' / 'copernicus' / 'cop_spb_3413_30m.tif'

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

# Load validation CSV (185 valid + 17 missing)
val_csv = PERSIST / 'spb_passport_validation_v3.csv'
if val_csv.exists():
    val = pd.read_csv(val_csv)
    print(f'Valid points (n={len(val)}):')
    print(f'  lat range: {val.lat.min():.5f}..{val.lat.max():.5f}')
else:
    print('No validation CSV!'); sys.exit(1)

# Find missing: passport points NOT in validation
val_keys = set(zip(val.lat.round(5), val.lon.round(5)))
missing_mask = ~pp.apply(lambda r: (round(r.lat, 5), round(r.lon, 5)) in val_keys, axis=1)
missing = pp[missing_mask].reset_index(drop=True)
print(f'\nMissing points: {len(missing)}/{len(pp)}')
print(f'  hp distribution: {missing.hp.describe()}')

# Load Copernicus
with rasterio.open(COP_TIF) as src:
    cop_inv = ~src.transform
    cop_shape = (src.height, src.width)
    cop_arr = src.read(1).astype('float32')
    cop_arr[cop_arr == src.nodata] = np.nan

# Get Copernicus at each missing point
cop_vals = []
for _, p in missing.iterrows():
    tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
    x, y = tr.transform(p.lon, p.lat)
    r, c = int(round((cop_inv * (x, y))[1])), int(round((cop_inv * (x, y))[0]))
    if 0 <= r < cop_shape[0] and 0 <= c < cop_shape[1]:
        v = cop_arr[r, c]
        cop_vals.append(float(v) if np.isfinite(v) else np.nan)
    else:
        cop_vals.append(np.nan)
missing['cop'] = cop_vals

# Load OSM water
print('\nLoading OSM water polygons...')
with open(WATER_GJSON) as f:
    gj = json.load(f)
water_polys_4326 = []
for ft in gj['features']:
    if ft['geometry']['type'] != 'Polygon': continue
    g = sg.shape(ft['geometry'])
    if not g.is_valid: g = g.buffer(0)
    if not g.is_empty: water_polys_4326.append(g)
print(f'  {len(water_polys_4326)} water polys')

# Project to EPSG:3413
proj = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True).transform
water_3413 = [so.transform(proj, p) for p in water_polys_4326 if p is not None and not p.is_empty]

# Check if each missing point is in water
print('\n=== 17 MISSING POINTS ===')
print(f'{"lat":>10} {"lon":>10} {"hp":>6} {"cop":>6} {"in_water":>10} {"dist_m":>7} {"verdict":>20}')
print('-' * 90)

results = []
for _, p in missing.iterrows():
    tr = pyproj.Transformer.from_crs('EPSG:4326', 'EPSG:3413', always_xy=True)
    x, y = tr.transform(p.lon, p.lat)
    pt = sg.Point(x, y)
    
    # Check if point is in any water polygon
    in_water = any(wp.contains(pt) or wp.touches(pt) for wp in water_3413)
    
    # Distance to nearest water polygon
    dist_to_water = min((pt.distance(wp) for wp in water_3413), default=99999)
    
    # Verdict based on cop + water
    if pd.isna(p.cop):
        verdict = 'no_cop_data'
    elif p.cop < 0.5:
        verdict = 'WATER (cop<0.5)'
    elif in_water:
        verdict = 'WATER (OSM)'
    elif dist_to_water < 20:
        verdict = f'near water ({dist_to_water:.0f}m)'
    else:
        verdict = 'on land'
    
    results.append({
        'lat': float(p.lat), 'lon': float(p.lon), 'hp': float(p.hp),
        'cop_m': float(p.cop) if not pd.isna(p.cop) else None,
        'in_water_osm': bool(in_water),
        'dist_to_water_m': round(float(dist_to_water), 1),
        'verdict': verdict,
    })
    print(f'{p.lat:>10.5f} {p.lon:>10.5f} {p.hp:>6.1f} '
          f'{(str(round(p.cop, 1)) if not pd.isna(p.cop) else "NaN"):>6} '
          f'{str(in_water):>10} {dist_to_water:>7.1f} {verdict:>20}')

df = pd.DataFrame(results)
print(f'\n=== VERDICT DISTRIBUTION ===')
print(df.verdict.value_counts())

# Stats
print(f'\n=== STATS ===')
print(f'  In water (OSM): {df.in_water_osm.sum()}/{len(df)}')
print(f'  Cop<0.5:        {(df.cop_m < 0.5).sum()}/{len(df)}')
print(f'  Cop NaN:        {df.cop_m.isna().sum()}/{len(df)}')
print(f'  On land (cop>=0.5 and not in water): {((df.cop_m >= 0.5) & (~df.in_water_osm)).sum()}/{len(df)}')

# Save
df.to_csv(str(OUT / 'spb_missing_17_analysis.csv'), index=False)
df.to_csv(str(PERSIST / 'spb_missing_17_analysis.csv'), index=False)
print(f'\n✓ Saved: spb_missing_17_analysis.csv')
