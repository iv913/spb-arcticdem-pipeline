#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30f — автодеталь GCP по центроидам зданий (план <-> OSM), робастный аффинный рефит.
Выход: georef_6.30.json (обновлён), GeoTIFF переписан, qa-кропы."""
import json
import numpy as np
from PIL import Image
from scipy import ndimage
import rasterio
from rasterio.transform import Affine
from rasterio.features import shapes as rio_shapes

OUTD = '/tmp/my-project/results/atayanc_6.30'
SRC = '/home/z/my-project/upload/atayanc-2013-01.jpg'
MLON = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0

MANUAL = [
    (858, 66,   30.29567, 59.95168, 2.0, 'Князь-Владимирский собор'),
    (2363, 275, 30.31648, 59.95020, 2.0, 'Петропавловский собор'),
    (1444, 831, 30.30340, 59.94628, 1.5, 'Биржевой мост, середина'),
    (1545, 1062, 30.30505, 59.94467, 1.5, 'Ростральная колонна (сев.)'),
    (2318, 1860, 30.31575, 59.93980, 1.5, 'Александровская колонна'),
]

im = np.asarray(Image.open(SRC).convert('RGB')).astype(np.int16)
H, W = im.shape[:2]
hsv = np.asarray(Image.open(SRC).convert('HSV')).astype(np.int16)
Hh, Ss, Vv = hsv[..., 0], hsv[..., 1], hsv[..., 2]
bmask = (Hh >= 3) & (Hh <= 38) & (Ss >= 28) & (Vv >= 95) & (Vv <= 250)
bmask = ndimage.binary_closing(bmask, np.ones((3, 3)))
lab, nlab = ndimage.label(bmask)
sizes = ndimage.sum(bmask, lab, range(1, nlab + 1))
keep = np.zeros(nlab, bool)
keep[:] = sizes >= 250   # >= ~140 м2
com = np.array(ndimage.center_of_mass(bmask, lab, range(1, nlab + 1)))
cy, cx = com[:, 0], com[:, 1]
area_px = sizes
print(f'плановых компонент зданий: {(keep).sum()} из {nlab}')

bjson = json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))
osm = []
for f in bjson['features']:
    g = np.array(f['geometry']['coordinates'][0], float)
    x = g[:, 0] - 30.30
    y = g[:, 1] - 59.945
    # площадь через шнурки в градусах -> м2
    a_deg = 0.5 * abs(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))
    a = a_deg * MLON * MLAT
    if a < 250 or a > 40000:
        continue
    osm.append((g[:, 0].mean() * MLON, g[:, 1].mean() * MLAT, a))
osm = np.array(osm)
print(f'OSM зданий-кандидатов: {len(osm)}')

def fit_affine(P, LL, Wt):
    A = np.zeros((2 * len(P), 6))
    for i, ((x, y), wi) in enumerate(zip(P, Wt)):
        A[2 * i] = [x, y, 1, 0, 0, 0]
        A[2 * i + 1] = [0, 0, 0, x, y, 1]
    Wm = np.repeat(np.sqrt(Wt), 2)
    A *= Wm[:, None]
    rhs = Wm * np.stack([np.array([p[0] for p in LL]), np.array([p[1] for p in LL])], 1).ravel()
    p, *_ = np.linalg.lstsq(A, rhs, rcond=None)
    return p  # a,b,c,d,e,f : E=a x+b y+c ; N=d x+e y+f

def resid(p, P, LL):
    E = p[0] * P[:, 0] + p[1] * P[:, 1] + p[2]
    N = p[3] * P[:, 0] + p[4] * P[:, 1] + p[5]
    return np.hypot(E - LL[:, 0], N - LL[:, 1])

# старт: 5 ручных GCP (вес х3 — ручные пики надёжнее автопар)
P = np.array([(g[0], g[1]) for g in MANUAL], float)
LL = np.array([(g[2] * MLON, g[3] * MLAT) for g in MANUAL], float)
Wt = np.array([g[4] * 3.0 for g in MANUAL], float)
p = fit_affine(P, LL, Wt)
print('старт RMS(5 ручных):', resid(p, P, LL).round(1))

# итерации автодетали
for it in range(3):
    E_pred = p[0] * cx + p[1] * cy + p[2]
    N_pred = p[3] * cx + p[4] * cy + p[5]
    pairs, used = [], set()
    for oe, on, oa in osm:
        # ожидаемый px
        # обратная задача 2x2
        M = np.array([[p[0], p[1]], [p[3], p[4]]])
        try:
            dx, dy = np.linalg.solve(M, np.array([oe - p[2], on - p[5]]))
        except np.linalg.LinAlgError:
            continue
        if not (0 <= dx < W and 0 <= dy < H):
            continue
        # ближайшая плановая компонента
        d = np.hypot(cx[keep] - dx, cy[keep] - dy)
        idxs = np.where(keep)[0] + 1
        j = np.argmin(d)
        if d[j] > 45:
            continue
        cid = idxs[j]
        if cid in used:
            continue
        a_m2 = area_px[cid - 1] * 0.784 * 0.763
        if not (250 <= area_px[cid - 1] <= 2200):
            continue
        if not (0.7 <= a_m2 / oa <= 1.4):
            continue
        used.add(cid)
        pairs.append((cx[cid - 1], cy[cid - 1], oe, on, min(oa, 4000) / 4000 * 1.0))
    if len(pairs) < 30:
        print(f'iter{it}: пар мало ({len(pairs)}) — стоп')
        break
    Pa = np.array([(q[0], q[1]) for q in pairs], float)
    A2 = np.array([(q[2], q[3]) for q in pairs], float)
    Wa = np.array([q[4] for q in pairs], float) * 0.6
    # робастно: ручные всегда, автопары с отбраковкой выбросов
    keepmask = np.ones(len(Pa), bool)
    for rb in range(3):
        pall = np.vstack([P, Pa[keepmask]])
        lall = np.vstack([LL, A2[keepmask]])
        wall = np.concatenate([Wt, Wa[keepmask]])
        p = fit_affine(pall, lall, wall)
        rr = resid(p, Pa[keepmask], A2[keepmask])
        mad = np.median(rr) if len(rr) else 0
        thr = max(3.0 * mad + 5, 22)
        new = rr <= thr
        if new.all() or not new.any():
            break
        keepmask[np.where(keepmask)[0][~new]] = False
    rman = resid(p, np.array([(g[0], g[1]) for g in MANUAL], float),
                 np.array([(g[2] * MLON, g[3] * MLAT) for g in MANUAL], float))
    print(f'iter{it}: пар {len(pairs)} (keep {keepmask.sum()}), RMS(ручные) {rman.round(1)}')

# финальная статистика по ручным GCP
rr_all = resid(p, np.array([(g[0], g[1]) for g in MANUAL], float),
               np.array([(g[2] * MLON, g[3] * MLAT) for g in MANUAL], float))
print('финал: RMS(ручные) =', round(float(np.sqrt((rr_all**2).mean())), 1), 'м; по точкам:',
      dict(zip([g[5] for g in MANUAL], rr_all.round(1))))
a, b, c, d, e, f = p
print(f'масштаб X {np.hypot(a, d):.4f} м/px, Y {np.hypot(b, e):.4f} м/px, поворот {np.degrees(np.arctan2(b, a)):+.2f}°')
aff = Affine(a / MLON, b / MLON, c / MLON, d / MLAT, e / MLAT, f / MLAT)
print('affine deg:', aff)

prof = dict(driver='GTiff', width=W, height=H, count=3, dtype='uint8',
            crs='EPSG:4326', transform=aff, compress='deflate', photometric='RGB')
with rasterio.open(f'{OUTD}/atayanc_plan_georef_6.30.tif', 'w', **prof) as dst:
    dst.write(im.transpose(2, 0, 1).astype(np.uint8))
bnds = rasterio.transform.array_bounds(H, W, aff)
print('bounds:', tuple(round(v, 6) for v in bnds))
json.dump({'affine_deg': list(aff)[:6], 'scale_mpx_x': float(np.hypot(a, d)),
           'scale_mpx_y': float(np.hypot(b, e)),
           'rms_m': float(np.sqrt((rr_all**2).mean())), 'mad_m': float(np.median(rr_all)),
           'n_gcp': int(len(P)), 'n_manual': len(MANUAL)},
          open(f'{OUTD}/georef_6.30.json', 'w'), ensure_ascii=False, indent=1)

# QA-кропы 2 уровней: стрелка и Дворцовая
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
plan_img = Image.open(SRC).convert('RGB')
fig, axs = plt.subplots(1, 2, figsize=(20, 9), constrained_layout=True)
for ax, (cx0, cy0, half) in zip(axs, [(1520, 1100, 380), (2230, 1750, 380)]):
    box = (cx0 - half, cy0 - half, cx0 + half, cy0 + half)
    crop = plan_img.crop(box)
    lo, la = (p[0] * box[0] + p[1] * box[1] + p[2]) / MLON, (p[3] * box[0] + p[4] * box[1] + p[5]) / MLAT
    hi, hb = (p[0] * box[2] + p[1] * box[3] + p[2]) / MLON, (p[3] * box[2] + p[4] * box[3] + p[5]) / MLAT
    ax.imshow(crop, extent=[min(lo, hi), max(lo, hi), min(la, hb), max(la, hb)],
              aspect=1 / np.cos(np.radians(59.945)), alpha=0.75)
    ax.set_xlim(min(lo, hi), max(lo, hi)); ax.set_ylim(min(la, hb), max(la, hb))
    ax.grid(color='k', alpha=0.3, lw=0.4)
    ax.set_title(f'план @ ({cx0},{cy0}) — сетка WGS84', fontsize=11)
fig.savefig(f'{OUTD}/qa_georef_zoom.png', dpi=150)
print('QA: qa_georef_zoom.png')
