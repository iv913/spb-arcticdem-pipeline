#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.53b — измерение рампы уреза на участке Атаянс (выбор ширины полосы B).

Профиль base/ground (DSM v2 без крыш) по бинам расстояния до воды OSM (0-80 м, 2 м):
p10/p25/p50/p75/p90 в каждом бине. B = первый бин, где p50 достигает плато
(плато = p50 в 60-100 м). Плюс: отметки набережных (dist<45 м) — до/после
восстановления. Плюс: распределение base на акватории (для порога суши).
"""
import json, csv
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from scipy import ndimage

FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
GEO = '/home/z/my-project/download/atayanc_6.30/georef_6.30.json'
PB = [30.283288502978362, 59.93759035347949, 30.325441520283814, 59.952163132920404]
OUTD = '/tmp/my-project/results/atayanc_6.53'
import os; os.makedirs(OUTD, exist_ok=True)

geo = json.load(open(GEO)); A = RAff(*geo['affine_deg'])
with rasterio.open(FUSE) as fs:
    b = fs.bounds
    corners = [(A.c, A.f), (A.c + A.a * 3000, A.f + A.d * 3000),
               (A.c + A.b * 2121, A.f + A.e * 2121),
               (A.c + A.a * 3000 + A.b * 2121, A.f + A.d * 3000 + A.e * 2121)]
    xs = [c[0] for c in corners]; ys = [c[1] for c in corners]
    x0 = max(b.left, min(xs)); y0 = max(b.bottom, min(ys))
    x1 = min(b.right, max(xs)); y1 = min(b.top, max(ys))
    win = rasterio.windows.from_bounds(x0, y0, x1, y1, fs.transform)
    terr = fs.read(1, window=win).astype(np.float32) / 100.0
    ttr = fs.window_transform(win)
terr[(terr < -10) | (terr > 90)] = np.nan
MLON = 111320.0 * np.cos(np.radians(59.945)); MLAT = 111132.0
W1 = int((x1 - x0) * MLON); H1 = int((y1 - y0) * MLAT)
aff1 = RAff(1 / MLON, 0, x0, 0, -1 / MLAT, y1)
dst = np.zeros((H1, W1), np.float32)
reproject(terr, dst, src_transform=ttr, src_crs='EPSG:4326',
          dst_transform=aff1, dst_crs='EPSG:4326', resampling=Resampling.bilinear)
base = dst.copy(); base[base < -50] = np.nan
print('base 1 м: %s' % (str(base.shape),))

# ground (v2, без крыш) — короткая версия коррекции: читаем готовый DSM 6.52
# (dsm = ground + roofs; крыши в полосе dist<45 у воды нет — для профиля сойдёт,
#  а для строгого v3 пересоберём в 6.53c)
with rasterio.open('/tmp/my-project/results/atayanc_6.52/atayanc_plot_dsm_1m_6.52.tif') as rs:
    dsm52 = rs.read(1).astype(np.float32) / 100.0
    dsm52[dsm52 < -300] = np.nan

water_g = unary_union([shape(f['geometry']).buffer(0) for f in
                       json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))['features']])
BB = Polygon([(PB[0], PB[1]), (PB[2], PB[1]), (PB[2], PB[3]), (PB[0], PB[3])])
land = BB.difference(water_g)
land_m = rasterize([(mapping(land), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
wmask = rasterize([(mapping(water_g.intersection(BB)), 1)], out_shape=(H1, W1),
                  transform=aff1).astype(bool)
dist = ndimage.distance_transform_edt(~wmask)  # м/px

print('\n=== ПРОФИЛЬ base по расстоянию до воды (только land_m, finite) ===')
fin = np.isfinite(base)
print(f"{'bin':>10s} {'n':>8s} {'p10':>6s} {'p25':>6s} {'p50':>6s} {'p75':>6s} {'p90':>6s} | {'p50 dsm52':>9s}")
for lo in range(0, 100, 2):
    m = land_m & fin & (dist >= lo) & (dist < lo + 2)
    v = base[m]
    if v.size < 30: continue
    vd = dsm52[m & np.isfinite(dsm52)]
    print(f"{lo:>4d}-{lo+2:<5d} {v.size:>8d} {np.percentile(v,10):>6.2f} {np.percentile(v,25):>6.2f} "
          f"{np.percentile(v,50):>6.2f} {np.percentile(v,75):>6.2f} {np.percentile(v,90):>6.2f} | "
          f"{(np.percentile(vd,50) if vd.size>30 else float('nan')):>9.2f}")

# автоматический выбор B: плато по 60-100 м
plateau = np.nan
m = land_m & fin & (dist >= 60) & (dist < 100)
if m.sum() > 500: plateau = float(np.percentile(base[m], 50))
B = None
for lo in range(0, 80, 2):
    m = land_m & fin & (dist >= lo) & (dist < lo + 2)
    if m.sum() < 30: continue
    if np.percentile(base[m], 50) >= plateau - 0.30:
        B = lo; break
print(f'\nплато p50 (60-100 м) = {plateau:.2f} м -> B = {B} м')

print('\n=== base на АКВАТОРИИ (wmask) — порог суши ===')
v = base[wmask & fin]
print('n=%d p10=%.2f p25=%.2f p50=%.2f p75=%.2f p90=%.2f' % (
    v.size, *np.percentile(v, [10, 25, 50, 75, 90])))
v2 = base[wmask & fin & (dist > 5)]  # глубже 5 м от кромки
print('глубже 5 м от кромки: p50=%.2f p90=%.2f' % (np.percentile(v2, 50), np.percentile(v2, 90)))

print('\n=== ОТМЕТКИ набережных (dist<45 м от воды) ===')
rows = list(csv.DictReader(open(CAT, encoding='utf-8')))
mx = np.array([float(r['lon']) for r in rows]); my = np.array([float(r['lat']) for r in rows])
mv = np.array([float(r['h_bs77_m']) for r in rows])
px_x = (mx - x0) * MLON; px_y = (y1 - my) * MLAT
inb = (px_x >= 0) & (px_x < W1) & (px_y >= 0) & (px_y < H1)
r_i = px_y[inb].astype(int); c_i = px_x[inb].astype(int)
dw = dist[r_i, c_i]
near = inb_mask = dw < 45
bv = base[np.clip(r_i,0,H1-1), np.clip(c_i,0,W1-1)]
dv = dsm52[np.clip(r_i,0,H1-1), np.clip(c_i,0,W1-1)]
print(f"{'id':>5s} {'h_отм':>6s} {'base':>6s} {'dsm52':>6s} {'dist':>5s}")
for j in np.where(near)[0]:
    print(f"{rows[j]['id'] if 'id' in rows[j] else j:>5s} {mv[j]:>6.2f} "
          f"{bv[j]:>6.2f} {dv[j]:>6.2f} {dw[j]:>5.0f}")
print('\nмедиана отметок набережных: %.2f м (n=%d); base против них: медиана ошибки %+.2f' % (
    np.median(mv[near]), near.sum(), np.median((bv - mv)[near & np.isfinite(bv)])))
