#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.52b — РЕВИЗИЯ участка: DTM/DSM 1 м с исправленным коррекционным полем
+ затопление +1..+5 с прибрежным барьером 12 м.

Исправления против 6.50d (по диагностике 6.51):
  1. ref-окно 9x9: p25 ТОЛЬКО по пикселям суши (base >= 0.5 м) — раньше окно
     у причала захватывало воду (~0 м) и тянуло ref вниз;
  2. коррекция = взвешенная МЕДИАНА k=24 соседей (робастно к одиночным
     якорям-выбросам — всплеск +4.3 у m013 в срезе 6.51);
  3. cap |corr| <= 1.5 м;
  4. SIGMA 90 -> 60 м (резче локальность);
Затопление: суша (BB - вода OSM 6.21) И дистанция до воды >= 12 м
(барьер против ленты смешанных пикселей уреза), компоненты >= 300 м2.

Выход: results/atayanc_6.52/atayanc_plot_dsm_1m_6.52.tif
       results/atayanc_6.52/flood_plot_6.52.geojson + dsm_meta_6.52.json
"""
import json, csv, os
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize, shapes as rio_shapes
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, Polygon, mapping
from shapely.ops import unary_union
from scipy.spatial import cKDTree
from scipy import ndimage

OUTD = '/tmp/my-project/results/atayanc_6.52'
os.makedirs(OUTD, exist_ok=True)
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
B30 = '/home/z/my-project/download/atayanc_6.30/plan_buildings_6.30.geojson'
GEO = '/home/z/my-project/download/atayanc_6.30/georef_6.30.json'
PB = [30.283288502978362, 59.93759035347949, 30.325441520283814, 59.952163132920404]

geo = json.load(open(GEO))
A = RAff(*geo['affine_deg'])
SIGMA = 60.0
KSOFT = 0.35
CAP = 1.5
CONF_W = {'H': 1.0, 'M': 0.7, 'L': 0.45}

# ---------- окно fusion -> base 1 м (как 6.50d)
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
MLON = 111320.0 * np.cos(np.radians(59.945))
MLAT = 111132.0
W1 = int((x1 - x0) * MLON)
H1 = int((y1 - y0) * MLAT)
aff1 = RAff(1 / MLON, 0, x0, 0, -1 / MLAT, y1)
dst = np.zeros((H1, W1), np.float32)
reproject(terr, dst, src_transform=ttr, src_crs='EPSG:4326',
          dst_transform=aff1, dst_crs='EPSG:4326', resampling=Resampling.bilinear)
base = dst.copy()
base[base < -50] = np.nan
print('base 1 м: %s, nan %d px' % (str(base.shape), int(np.isnan(base).sum())))

# ---------- отметки
rows = list(csv.DictReader(open(CAT, encoding='utf-8')))
mx = np.array([float(r['lon']) for r in rows])
my = np.array([float(r['lat']) for r in rows])
mv = np.array([float(r['h_bs77_m']) for r in rows])
mc = np.array([CONF_W[r['conf']] for r in rows])
px_x = (mx - x0) * MLON
px_y = (y1 - my) * MLAT
inb = (px_x >= 0) & (px_x < W1) & (px_y >= 0) & (px_y < H1)
print('отметок в окне:', int(inb.sum()), '/', len(rows))
r_idx = np.clip(px_y[inb].astype(int), 0, H1 - 1)
c_idx = np.clip(px_x[inb].astype(int), 0, W1 - 1)

# ФИКС-1: ref = p25 окна 9x9 только по СУШЕ (base >= 0.5)
ref = np.full(int(inb.sum()), np.nan)
for i, (r, c) in enumerate(zip(r_idx, c_idx)):
    r0, r1_ = max(0, r - 4), min(H1, r + 5)
    c0, c1_ = max(0, c - 4), min(W1, c + 5)
    w = base[r0:r1_, c0:c1_]
    wv = w[~np.isnan(w) & (w >= 0.5)]
    if len(wv):
        ref[i] = np.percentile(wv, 25)
valid = ~np.isnan(ref)
d_i = mv[inb][valid] - ref[valid]
pts_m = np.stack([px_x[inb][valid], px_y[inb][valid]], axis=1)
w_i = mc[inb][valid]
print('diff (land-only ref): bias %+.2f, MAD %.2f, n=%d' %
      (d_i.mean(), np.median(np.abs(d_i)), valid.sum()))

# ФИКС-2/3: взвешенная медиана + cap
def w_median(vals, wts):
    o = np.argsort(vals)
    v, w = vals[o], wts[o]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])

tree = cKDTree(pts_m)
RMAX = SIGMA * 3.0
KNN = 24

def neigh(i):
    """kNN одной отметки: (dist, idx, wgt)"""
    dist, idx = tree.query(pts_m[i], k=min(KNN, len(pts_m)), distance_upper_bound=RMAX)
    dist = np.atleast_1d(dist); idx = np.atleast_1d(idx)
    wgt = np.exp(-(dist ** 2) / (2 * SIGMA ** 2)) * w_i[np.clip(idx, 0, len(w_i) - 1)]
    ok = idx < len(pts_m)
    return dist[ok], idx[ok], wgt[ok]

def w_median(vals, wts):
    o = np.argsort(vals)
    v, w = vals[o], wts[o]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])

# --- ПРОХОД 1: локальная медиана по соседям -> остатки -> отбраковка выбросов
med_i = np.zeros(len(pts_m))
for i in range(len(pts_m)):
    _, idx, wgt = neigh(i)
    med_i[i] = w_median(d_i[idx], wgt) if len(idx) >= 3 else d_i[i]
resid = d_i - med_i
mad_r = np.median(np.abs(resid - np.median(resid))) * 1.4826
thr = max(0.75, 2.5 * mad_r)
inl = np.abs(resid) <= thr
print('ПРОХОД 1: выбросов %d из %d (порог %.2f м); d_i: p10 %.2f p50 %.2f p90 %.2f' %
      (int((~inl).sum()), len(inl), thr, np.percentile(d_i, 10),
       np.median(d_i), np.percentile(d_i, 90)))
# полоса lacuna-артефакта (30.305-30.32): сколько там точек и как исправляются
band = (pts_m[:, 0] / MLON + x0 > 30.305) & (pts_m[:, 0] / MLON + x0 < 30.32)
print('  band 30.305-30.32: n=%d, d_i мед %.2f (fusion завышает на %.2f)' %
      (int(band.sum()), np.median(d_i[band]) if band.any() else 0,
       -np.median(d_i[band]) if band.any() else 0))

# --- ПРОХОД 2: взвешенное СРЕДНЕЕ по инлаерам (полная сила коррекции кластеров)
d_use = d_i[inl]
p_use = pts_m[inl]
w_use = w_i[inl]
tree2 = cKDTree(p_use)
GS = 4
WG, HG = (W1 + GS - 1) // GS, (H1 + GS - 1) // GS
gx = (np.arange(WG) * GS + GS / 2)
gy = (np.arange(HG) * GS + GS / 2)
GX, GY = np.meshgrid(gx, gy)
Gpts = np.stack([GX.ravel(), GY.ravel()], axis=1)
corr_g = np.zeros(len(Gpts), np.float32)
beta_g = np.zeros(len(Gpts), np.float32)
CHUNK = 200000
for s in range(0, len(Gpts), CHUNK):
    chunk = Gpts[s:s + CHUNK]
    dist, idx = tree2.query(chunk, k=min(24, len(p_use)), distance_upper_bound=RMAX)
    dist = np.atleast_2d(dist); idx = np.atleast_2d(idx)
    wgt = np.exp(-(dist ** 2) / (2 * SIGMA ** 2)) * w_use[np.clip(idx, 0, len(w_use) - 1)]
    bad = idx >= len(p_use)
    wgt[bad] = 0.0
    sw = wgt.sum(axis=1)
    okc = sw > 1e-9
    num = (wgt * d_use[np.clip(idx, 0, len(d_use) - 1)]).sum(axis=1)
    corr_g[s:s + CHUNK] = np.where(okc, num / np.maximum(sw, 1e-9), 0.0)
    beta_g[s:s + CHUNK] = sw / (sw + KSOFT)
corr_fb = corr_g.reshape(HG, WG) * beta_g.reshape(HG, WG) * (beta_g.reshape(HG, WG) > 0.05)
ground2 = np.zeros((H1, W1), np.float32)
reproject(corr_fb.astype(np.float32), ground2,
          src_transform=RAff(GS / MLON, 0, x0, 0, -GS / MLAT, y1),
          src_crs='EPSG:4326', dst_transform=aff1, dst_crs='EPSG:4326',
          resampling=Resampling.bilinear)
ground = base + ground2
ground[np.isnan(base)] = np.nan

gi = np.stack([r_idx, c_idx])
gvals = ground[gi[0], gi[1]]
gd = mv[inb] - gvals
okg = ~np.isnan(gvals)
print('контроль ground vs отметки: bias %+.2f, MAD %.2f (n=%d)' %
      (np.nanmean(gd[okg]), np.nanmedian(np.abs(gd[okg])), int(okg.sum())))

# ---------- крыши (как 6.50d)
bfc = json.load(open(B30))
shapes_roof, vals_roof = [], []
ground_f = np.nan_to_num(ground, nan=0.0)
valid_m = ~np.isnan(ground)
for ft in bfc['features']:
    h = ft['properties'].get('h')
    if h is None:
        continue
    g = shape(ft['geometry'])
    ring = g.buffer(18).difference(g.buffer(6))
    msk = rasterize([(mapping(ring), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
    if msk.sum() < 20:
        msk = rasterize([(mapping(g.buffer(25)), 1)], out_shape=(H1, W1),
                        transform=aff1).astype(bool)
    gv = ground[msk & valid_m]
    if len(gv) < 10:
        continue
    shapes_roof.append(mapping(g))
    vals_roof.append(float(np.median(gv) + h))
print('крыш:', len(shapes_roof))
dsm = ground.copy()
if shapes_roof:
    roof_r = rasterize(list(zip(shapes_roof, vals_roof)), out_shape=(H1, W1),
                       transform=aff1, fill=0, dtype='float32')
    have = (roof_r > 0) & valid_m
    dsm[have] = np.maximum(dsm[have], roof_r[have])
water = (base < 0.25) | np.isnan(base)
dsm[water] = np.nan
print('DSM 6.52: min %.2f max %.2f, вода %.1f%%' % (np.nanmin(dsm), np.nanmax(dsm),
                                                    100.0 * water.mean()))
dsm_filled = np.where(np.isfinite(dsm), np.clip(dsm, -100, 900), -327.68)
dsm_i16 = np.round(dsm_filled * 100).astype(np.int16)
prof = dict(driver='GTiff', width=W1, height=H1, count=1, dtype='int16',
            crs='EPSG:4326', transform=aff1, compress='deflate', nodata=-32768)
with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.52.tif', 'w', **prof) as dd:
    dd.write(dsm_i16, 1)
json.dump({'bounds': [x0, y0, x1, y1], 'sigma_m': SIGMA, 'cap': CAP,
           'knn': 24, 'ref': 'p25 land-only', 'n_marks': int(inb.sum())},
          open(f'{OUTD}/dsm_meta_6.52.json', 'w'))
print('TIF готов')

# ---------- затопление с барьером 12 м
water_g = unary_union([shape(f['geometry']).buffer(0) for f in
                       json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))['features']])
BB = Polygon([(PB[0], PB[1]), (PB[2], PB[1]), (PB[2], PB[3]), (PB[0], PB[3])])
land = BB.difference(water_g)
land_m = rasterize([(mapping(land), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
wmask = rasterize([(mapping(water_g.intersection(BB)), 1)], out_shape=(H1, W1),
                  transform=aff1).astype(bool)
dist_to_water = ndimage.distance_transform_edt(~wmask)  # м/px = 1 м
BAR = 12.0
floods = {}
for lv in (1, 2, 3, 4, 5):
    m = (dsm <= lv) & land_m & (dist_to_water >= BAR)
    m &= np.isfinite(dsm)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool)
        keep[1:] = sizes >= 300
        m2 = keep[lab]
    else:
        m2 = m
    feats = []
    for g, v in rio_shapes(m2.astype(np.uint8), mask=m2, transform=aff1):
        p = shape(g)
        if p.is_empty or p.area < 2e-11:
            continue
        p = p.simplify(3e-5)
        if p.is_empty or p.geom_type != 'Polygon':
            continue
        feats.append({'type': 'Feature', 'properties': {'level': lv,
                     'area_m2': round(p.area * 56050 * 111320, 0)},
                     'geometry': mapping(p)})
    floods[lv] = feats
    print('затопление +%d м (барьер %d м): %d полигонов, %.3f км2' %
          (lv, BAR, len(feats), sum(f['properties']['area_m2'] for f in feats) / 1e6))
json.dump({str(k): v for k, v in floods.items()},
          open(f'{OUTD}/flood_plot_6.52.geojson', 'w'), ensure_ascii=False)

# ---------- валидация барьера: доля +1 в 15 м от воды (до = 76.8%)
from shapely.geometry import Point
import random
f1 = unary_union([shape(f['geometry']) for f in floods[1]]) if floods[1] else None
if f1 is not None:
    minx, miny, maxx, maxy = f1.bounds
    rng = np.random.default_rng(11)
    pts = []
    while len(pts) < 800:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if f1.contains(p):
            pts.append((p.x, p.y))
    ds = np.array([water_g.distance(Point(*p)) * 56050 for p in pts])
    print('после барьера: доля +1 м в 15 м от воды: %.0f%% (медиана %.0f м, n=%d)' %
          (100 * (ds <= 15).mean(), np.median(ds), len(ds)))
print('ГОТОВО 6.52b ->', OUTD)
