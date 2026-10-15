#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30e — GCP-подгонка плана Атаянс (similarity+affine), GeoTIFF, QA-оверлей.
GCP пикованы по однозначным подписанным объектам (кропы g1-g4, c1-c6)."""
import json
import numpy as np
from PIL import Image
import rasterio
from rasterio.transform import Affine

OUTD = '/tmp/my-project/results/atayanc_6.30'
SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'

# метры в градусах (центр участка)
MLON = 111320.0 * np.cos(np.radians(59.945))   # 56047
MLAT = 111132.0

# (px, py) -> (lon, lat), вес
GCP = [
    (858, 66,   30.29567, 59.95168, 1.0, 'Князь-Владимирский собор'),
    (2363, 275, 30.31648, 59.95020, 1.0, 'Петропавловский собор'),
    (1444, 831, 30.30340, 59.94628, 1.0, 'Биржевой мост, середина'),
    (1545, 1062, 30.30505, 59.94467, 1.0, 'Ростральная колонна (сев.)'),
    (2318, 1860, 30.31575, 59.93980, 1.0, 'Александровская колонна'),
    (1512, 1224, 30.30461, 59.94350, 0.5, 'Биржа (ЦВММ)'),
    (2178, 1658, 30.31412, 59.94034, 0.5, 'Зимний дворец'),
]

px = np.array([(g[0], g[1]) for g in GCP], float)
lonlat = np.array([(g[2], g[3]) for g in GCP], float)
w = np.array([g[4] for g in GCP])
E = lonlat[:, 0] * MLON   # метры восток
N = lonlat[:, 1] * MLAT   # метры север


def fit(model):
    # px,py -> (E,N)
    if model == 'similarity':
        # E = a*x + b*y + c ; N = -b*x + a*y + d
        A = np.zeros((2 * len(px), 4))
        for i, ((x, y), wi) in enumerate(zip(px, w)):
            A[2 * i] = [x, y, 1, 0]
            A[2 * i + 1] = [-y, x, 0, 1]
        A *= np.repeat(w, 2)[:, None]
        rhs = np.repeat(w, 2) * np.stack([E, N], 1).ravel()
        p, *_ = np.linalg.lstsq(A, rhs, rcond=None)
        a, b, c, d = p
        def tr(x, y): return a * x + b * y + c, -b * x + a * y + d
        scale = np.hypot(a, b)
        rot = np.degrees(np.arctan2(b, a))
        return tr, scale, rot, p
    else:  # affine
        A = np.zeros((2 * len(px), 6))
        for i, ((x, y), wi) in enumerate(zip(px, w)):
            A[2 * i] = [x, y, 1, 0, 0, 0]
            A[2 * i + 1] = [0, 0, 0, x, y, 1]
        A *= np.repeat(w, 2)[:, None]
        rhs = np.repeat(w, 2) * np.stack([E, N], 1).ravel()
        p, *_ = np.linalg.lstsq(A, rhs, rcond=None)
        a, b, c, d, e, f = p
        def tr(x, y): return a * x + b * y + c, d * x + e * y + f
        return tr, None, None, p


for model in ('similarity', 'affine'):
    tr, scale, rot, p = fit(model)
    res = []
    for (x, y), (lo, la), wi, nm in zip(px, lonlat, w, [g[5] for g in GCP]):
        e, n = tr(x, y)
        dx = (e - lo * MLON) / MLON * MLON   # в метрах
        dy = n - la * MLAT
        res.append((nm, np.hypot(dx, dy)))
    r = np.array([x[1] for x in res])
    rms = np.sqrt(np.average(r**2, weights=w))
    print(f'== {model}: RMS = {rms:.2f} м ({rms/scale:.1f} px)' if scale else f'== {model}: RMS = {rms:.2f} м')
    if scale:
        print(f'   масштаб {scale:.4f} м/px, поворот {rot:+.2f}°')
    for nm, d in res:
        print(f'   {nm:30s} {d:6.1f} м')

tr, scale, rot, p = fit('affine')
a, b, c, d, e, f = p
# rasterio Affine: E = a_r*px + b_r*py + c_r ; N = d_r*px + e_r*py + f_r
aff = Affine(a, b, c / MLON, d / MLAT, e / MLAT, f / (MLAT * 1.0))
# переводим в градусы: E_deg = E_m/MLON = (a*px + b*py + c)/MLON
aff = Affine(a / MLON, b / MLON, c / MLON, d / MLAT, e / MLAT, f / MLAT)
print('\nAffine (deg):', aff)
print('scale м/px:', np.hypot(a, b), 'shear-ish:', np.hypot(d, e))

im = Image.open(SRC).convert('RGB')
prof = dict(driver='GTiff', width=im.width, height=im.height, count=3,
            dtype='uint8', crs='EPSG:4326', transform=aff,
            compress='deflate', photometric='RGB')
with rasterio.open(f'{OUTD}/atayanc_plan_georef_6.30.tif', 'w', **prof) as dst:
    dst.write(np.asarray(im).transpose(2, 0, 1))
bnds = rasterio.transform.array_bounds(im.height, im.width, aff)
print('bounds (4326):', tuple(round(v, 6) for v in bnds))
resids = []
for (x, y, lo, la, wi, nm) in GCP:
    e, n = tr(x, y)
    resids.append(float(np.hypot(e - lo * MLON, n - la * MLAT)))
rms_final = float(np.sqrt(np.average(np.array(resids) ** 2, weights=[g[4] for g in GCP])))
json.dump({'gcp': GCP, 'affine_deg': list(aff)[:6],
           'scale_mpx': float(np.hypot(a, b)), 'rot': float(rot) if rot else None,
           'residuals_m': resids, 'rms_m': rms_final},
          open(f'{OUTD}/georef_6.30.json', 'w'), ensure_ascii=False, indent=1, default=float)

# QA-оверлей: план (полупрозрачный) поверх рендера OSM
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
ref = plt.imread(f'{OUTD}/osm_reference_6.30.png')
plan = np.asarray(im.resize((1500, int(1500 * im.height / im.width))))
# bounds рендера OSM
RB = (30.245, 59.928, 30.355, 59.956)
fig, axs = plt.subplots(1, 2, figsize=(22, 11), constrained_layout=True)
for ax in axs:
    ax.imshow(ref, extent=[RB[0], RB[2], RB[1], RB[3]], aspect=1 / np.cos(np.radians(59.945)))
    ax.set_xlim(RB[0], RB[2]); ax.set_ylim(RB[1], RB[3])
b0, b1, b2, b3 = rasterio.transform.array_bounds(im.height, im.width, aff)
axs[1].imshow(plan, extent=[b0, b2, b1, b3], aspect=1 / np.cos(np.radians(59.945)), alpha=0.55)
axs[0].set_title('OSM reference', fontsize=11)
axs[1].set_title('OSM + план Атаянс (alpha 0.55)', fontsize=11)
for g in GCP:
    for ax in axs:
        ax.plot(g[2], g[3], 'r*', ms=10, mec='yellow')
fig.savefig(f'{OUTD}/qa_georef_overlay.png', dpi=150)
print('QA оверлей: qa_georef_overlay.png')
