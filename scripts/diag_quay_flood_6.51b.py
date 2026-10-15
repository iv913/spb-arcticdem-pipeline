#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.51b — визуальная диагностика ленты затопления +1 м вдоль набережных участка 6.50.
Профили поперёк причалов (fusion base + DSM 6.50) + гистограмма DSM внутри зоны +1 м."""
import json, csv, math, os
import numpy as np
import rasterio
from shapely.geometry import shape, Point
from shapely.ops import unary_union

import matplotlib.font_manager as fm
fm.fontManager.addfont('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plt.rcParams['font.sans-serif'] = ['DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

FLOOD = '/tmp/my-project/results/atayanc_6.50/flood_plot_6.50.geojson'
DSM   = '/tmp/my-project/results/atayanc_6.50/atayanc_plot_dsm_1m_6.50.tif'
FUSE  = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
WATER = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
CAT   = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
OUTD  = '/home/z/my-project/results/atayanc_6.51'
os.makedirs(OUTD, exist_ok=True)

fj = json.load(open(FLOOD))
def feats_for(lv):
    for k, v in fj.items():
        if float(k) == float(lv):
            return [shape(f['geometry']) for f in v]
    return []
lv1_u = unary_union(feats_for(1.0))

water_u = unary_union([shape(f['geometry']) for f in json.load(open(WATER))['features']])
DEG = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0

# ---------- A. DSM внутри зоны +1 м: значения высот в затопленной ленте
minx, miny, maxx, maxy = lv1_u.bounds
n = 4000
rng = np.random.default_rng(7)
pts = []
while len(pts) < 1200:
    p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
    if lv1_u.contains(p):
        pts.append((p.x, p.y))
with rasterio.open(DSM) as fs:
    dv = np.array([v[0] for v in fs.sample(pts)], dtype=np.float32) / 100.0
dv = dv[dv > -100]
print('DSM внутри зоны +1 м: n=%d, p10 %.2f / медиана %.2f / p90 %.2f' %
      (len(dv), np.percentile(dv, 10), np.median(dv), np.percentile(dv, 90)))
# расстояние до воды для тех же точек
dists = np.array([water_u.distance(Point(*p)) * DEG for p in pts])
print('расстояние до воды: медиана %.1f м, p90 %.1f м, <=15 м: %.0f%%' %
      (np.median(dists), np.percentile(dists, 90), 100 * (dists <= 15).mean()))

# ---------- B. Профили поперёк набережных
def sample(path, lons, lats, scale=100.0):
    with rasterio.open(path) as fs:
        v = np.array([x[0] for x in fs.sample(list(zip(lons, lats)))], dtype=np.float32)
    return v / scale

rows = list(csv.DictReader(open(CAT, encoding='utf-8')))
def nearest_water_dir(lon, lat):
    best = None
    for adeg in range(0, 360, 10):
        a = math.radians(adeg)
        for rm in (10, 20, 30, 40, 60, 80):
            dx = rm * math.sin(a) / DEG; dy = rm * math.cos(a) / MLAT
            if water_u.contains(Point(lon + dx, lat + dy)):
                if best is None or rm < best[1]:
                    best = (adeg, rm)
                break
    return best

# три показательных случая: низкая reading (m002), средняя (m013), высокая (m028)
PICK = ['m002', 'm013', 'm028']
byid = {r['id']: r for r in rows if r.get('id')}
offs = np.arange(-40, 61, 2.0)
fig, axes = plt.subplots(3, 1, figsize=(9.5, 8.2), constrained_layout=True)
fig.suptitle('Срезы поперёк набережных (участок Атаянс, 6.50): вода → берег', fontsize=12)
for ax, pid in zip(axes, PICK):
    r = byid[pid]
    lon, lat = float(r['lon']), float(r['lat'])
    h_mark = float(r['h_bs77_m'])
    nw = nearest_water_dir(lon, lat)
    adeg, rm = nw
    a = math.radians(adeg)
    xs = rm + offs
    lons = lon + xs * math.sin(a) / DEG
    lats = lat + xs * math.cos(a) / MLAT
    base = sample(FUSE, lons, lats)
    dsm = sample(DSM, lons, lats)
    ax.plot(offs, base, '-o', ms=2.5, lw=1.4, color='#2b7bba', label='fusion base (8 м сетка)')
    ax.plot(offs, dsm, '-s', ms=2.5, lw=1.2, color='#d67b1e', alpha=0.85, label='DSM 6.50 (1 м, с коррекцией)')
    ax.axhline(1.0, color='#c22', lw=1.6, ls='--', label='уровень +1 м')
    ax.axhline(h_mark, color='#2a2', lw=1.3, ls=':', label='отметка плана %s = %.2f м' % (pid, h_mark))
    ax.axvspan(-40, 0, color='#9ecae1', alpha=0.25)
    ax.text(-38, ax.get_ylim()[1] * 0.92, 'вода', fontsize=9, color='#225577')
    ax.set_ylabel('высота, м БС-77')
    ax.set_xlabel('расстояние от уреза, м (отрицательное = акватория)')
    ax.set_ylim(-0.6, max(4.2, h_mark + 0.6))
    ax.legend(fontsize=7.5, loc='upper left', framealpha=0.9)
    ax.grid(alpha=0.25)
fig.savefig(f'{OUTD}/quay_transects_6.51.png', dpi=150)
print('PNG:', f'{OUTD}/quay_transects_6.51.png')

# ---------- C. Какая доля +1 м ленты лежит ВНУТРИ воды OSM с точностью до буфера
for buf in (5, 10, 15):
    frac = lv1_u.intersection(water_u.buffer(buf / DEG)).area / max(lv1_u.area, 1e-15)
    print('+1 м: %2d м от воды OSM покрывает %5.1f%% зоны' % (buf, 100 * frac))
print('ГОТОВО 6.51b')
