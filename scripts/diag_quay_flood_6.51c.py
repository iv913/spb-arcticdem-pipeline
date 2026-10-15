#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.51c — корректные срезы поперёк набережных: профиль от отметки В СТОРОНУ воды
и далее в акваторию. Ось X = расстояние от отметки; урез по OSM подсвечен."""
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

DSM   = '/tmp/my-project/results/atayanc_6.50/atayanc_plot_dsm_1m_6.50.tif'
FUSE  = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
WATER = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
CAT   = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
OUTD  = '/home/z/my-project/results/atayanc_6.51'
os.makedirs(OUTD, exist_ok=True)

water_u = unary_union([shape(f['geometry']).buffer(0) for f in
                       json.load(open(WATER))['features']])
DEG = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0

def sample(path, lons, lats, scale=100.0):
    with rasterio.open(path) as fs:
        v = np.array([x[0] for x in fs.sample(list(zip(lons, lats)))], dtype=np.float32)
    return v / scale

rows = list(csv.DictReader(open(CAT, encoding='utf-8')))

def water_profile(lon, lat):
    """азимут на ближайшую воду и дистанцию уреза (шаг 2 м, скан 2°)"""
    for rm in np.arange(4, 160, 2.0):
        for adeg in range(0, 360, 4):
            a = math.radians(adeg)
            dx = rm * math.sin(a) / DEG
            dy = rm * math.cos(a) / MLAT
            if water_u.contains(Point(lon + dx, lat + dy)):
                return adeg, rm
    return None

PICK = ['m002', 'm013', 'm028']
byid = {r['id']: r for r in rows if r.get('id')}
fig, axes = plt.subplots(3, 1, figsize=(9.8, 8.6), constrained_layout=True)
fig.suptitle('Профили поперёк набережных (участок Атаянс 6.50): от отметки плана → к воде',
             fontsize=12)
for ax, pid in zip(axes, PICK):
    r = byid[pid]
    lon, lat = float(r['lon']), float(r['lat'])
    h_mark = float(r['h_bs77_m'])
    wp = water_profile(lon, lat)
    adeg, rm = wp
    a = math.radians(adeg)
    d = np.arange(-60, rm + 45, 2.0)          # расстояние ОТ ОТМЕТКИ вдоль азимута на воду
    lons = lon + d * math.sin(a) / DEG
    lats = lat + d * math.cos(a) / MLAT
    base = sample(FUSE, lons, lats)
    dsm = sample(DSM, lons, lats)
    ax.plot(d, base, '-o', ms=2.5, lw=1.4, color='#2b7bba', label='fusion A2 (2 м)')
    ax.plot(d, dsm, '-s', ms=2.5, lw=1.2, color='#d67b1e', alpha=0.85,
            label='DSM 6.50 (1 м, с коррекцией)')
    ax.axhline(1.0, color='#c22', lw=1.6, ls='--', label='уровень +1 м')
    ax.axhline(h_mark, color='#2a2', lw=1.3, ls=':',
               label='отметка %s = %.2f м' % (pid, h_mark))
    ax.axvspan(rm, d[-1], color='#9ecae1', alpha=0.30)
    ax.text(rm + 2, 0.12, 'акватория (OSM)', fontsize=8.5, color='#225577')
    ax.axvline(0, color='#666', lw=0.8, ls='-')
    ax.text(-58, ax.get_ylim()[1] * 0.95 - 0.3, 'отметка →', fontsize=8, color='#444')
    ax.set_ylabel('высота, м БС-77')
    ax.set_xlabel('расстояние от отметки, м (урез на ~%.0f м по азимуту %d°)' % (rm, adeg))
    ax.set_ylim(-0.7, max(4.3, h_mark + 0.7))
    ax.legend(fontsize=7.5, loc='upper left', framealpha=0.9)
    ax.grid(alpha=0.25)
fig.savefig(f'{OUTD}/quay_transects_6.51c.png', dpi=150)
print('PNG:', f'{OUTD}/quay_transects_6.51c.png')
print('ГОТОВО 6.51c')
