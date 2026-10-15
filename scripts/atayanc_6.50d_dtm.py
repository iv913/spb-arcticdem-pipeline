#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.50d — DTM/DSM 1 м участка: fusion A2 + коррекционное поле по 332 отметкам
плана (починка сглаженных lacuna-зон fusion), крыши из зданий 6.30, вода -> nodata."""
import json, csv, os
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, mapping
from scipy.spatial import cKDTree

OUTD = '/tmp/my-project/results/atayanc_6.50'
os.makedirs(OUTD, exist_ok=True)
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
B30 = '/home/z/my-project/download/atayanc_6.30/plan_buildings_6.30.geojson'
GEO = '/home/z/my-project/download/atayanc_6.30/georef_6.30.json'

geo = json.load(open(GEO))
A = RAff(*geo['affine_deg'])
SIGMA = 90.0      # м, масштаб коррекционного поля
KSOFT = 0.35      # мягкость бленда
CONF_W = {'H': 1.0, 'M': 0.7, 'L': 0.45}

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
print('окно fusion:', terr.shape)
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
print('base 1 м: min %.2f max %.2f (nan %d px)' % (
    np.nanmin(base), np.nanmax(base), int(np.isnan(base).sum())))

# --- коррекционное поле по отметкам
rows = list(csv.DictReader(open(CAT)))
mx = np.array([float(r['lon']) for r in rows])
my = np.array([float(r['lat']) for r in rows])
mv = np.array([float(r['h_bs77_m']) for r in rows])
mc = np.array([CONF_W[r['conf']] for r in rows])
PXM = 1.0  # м на пиксель сетки
px_x = (mx - x0) * MLON
px_y = (y1 - my) * MLAT
inb = (px_x >= 0) & (px_x < W1) & (px_y >= 0) & (px_y < H1)
print('отметок в окне:', int(inb.sum()), '/', len(rows))

r_idx = np.clip(px_y[inb].astype(int), 0, H1 - 1)
c_idx = np.clip(px_x[inb].astype(int), 0, W1 - 1)
ref = np.empty(int(inb.sum()))
for i, (r, c) in enumerate(zip(r_idx, c_idx)):
    r0, r1_ = max(0, r - 4), min(H1, r + 5)
    c0, c1_ = max(0, c - 4), min(W1, c + 5)
    w = base[r0:r1_, c0:c1_]
    wv = w[~np.isnan(w)]
    ref[i] = np.percentile(wv, 25) if len(wv) else np.nan
valid = ~np.isnan(ref)
d_i = mv[inb][valid] - ref[valid]
pts_m = np.stack([px_x[inb][valid], px_y[inb][valid]], axis=1) * PXM
w_i = mc[inb][valid]
print('diff: bias %+.2f, MAD %.2f, p95 %.2f' % (d_i.mean(), np.median(np.abs(d_i)),
                                                np.percentile(np.abs(d_i), 95)))

tree = cKDTree(pts_m)
RMAX = SIGMA * 3.0
# коррекционное поле считаем на редкой сетке 4 м, затем репроекция на 1 м
GS = 4
WG, HG = (W1 + GS - 1) // GS, (H1 + GS - 1) // GS
gx = (np.arange(WG) * GS + GS / 2) * PXM
gy = (np.arange(HG) * GS + GS / 2) * PXM
GX, GY = np.meshgrid(gx, gy)
Gpts = np.stack([GX.ravel(), GY.ravel()], axis=1)
corr_g = np.zeros(len(Gpts), np.float32)
beta_g = np.zeros(len(Gpts), np.float32)
CHUNK = 200000
for s in range(0, len(Gpts), CHUNK):
    chunk = Gpts[s:s + CHUNK]
    dist, idx = tree.query(chunk, k=min(64, len(pts_m)), distance_upper_bound=RMAX)
    dist = np.atleast_2d(dist); idx = np.atleast_2d(idx)
    wgt = np.exp(-(dist ** 2) / (2 * SIGMA ** 2)) * w_i[np.clip(idx, 0, len(w_i) - 1)]
    wgt[idx >= len(pts_m)] = 0.0
    sw = wgt.sum(axis=1)
    num = (wgt * d_i[np.clip(idx, 0, len(d_i) - 1)]).sum(axis=1)
    okc = sw > 1e-9
    corr_g[s:s + CHUNK] = np.where(okc, num / np.maximum(sw, 1e-9), 0.0)
    beta_g[s:s + CHUNK] = sw / (sw + KSOFT)
corr_f = corr_g.reshape(HG, WG) * (beta_g.reshape(HG, WG) > 0.05)
# бленд: ground = base + beta * corr (beta * corr репроецируется на сетку 1 м)
corr_f_beta = corr_g.reshape(HG, WG) * beta_g.reshape(HG, WG) * \
    (beta_g.reshape(HG, WG) > 0.05)
ground2 = np.zeros((H1, W1), np.float32)
reproject(corr_f_beta.astype(np.float32), ground2, src_transform=RAff(GS / MLON, 0, x0,
          0, -GS / MLAT, y1), src_crs='EPSG:4326', dst_transform=aff1,
          dst_crs='EPSG:4326', resampling=Resampling.bilinear)
ground = base + ground2
ground[np.isnan(base)] = np.nan
print('ground после коррекции: min %.2f max %.2f' % (np.nanmin(ground),
                                                      np.nanmax(ground)))

# контроль на отметках
gi = np.stack([np.clip(px_y[inb].astype(int), 0, H1 - 1),
               np.clip(px_x[inb].astype(int), 0, W1 - 1)])
gvals = ground[gi[0], gi[1]]
gd = mv[inb] - gvals
okg = ~np.isnan(gvals)
print('контроль ground vs отметки: bias %+.2f, MAD %.2f (n=%d)' % (
    np.nanmean(gd[okg]), np.nanmedian(np.abs(gd[okg])), int(okg.sum())))

# --- крыши
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
print('DSM: min %.2f max %.2f, вода %.1f%%' % (np.nanmin(dsm), np.nanmax(dsm),
                                               100.0 * water.mean()))
dsm_valid = ~np.isnan(dsm)
dsm_filled = np.where(dsm_valid, np.clip(dsm, -100, 900), -327.68)
dsm_i16 = np.round(dsm_filled * 100).astype(np.int16)
prof = dict(driver='GTiff', width=W1, height=H1, count=1, dtype='int16',
            crs='EPSG:4326', transform=aff1, compress='deflate', nodata=-32768)
outt = f'{OUTD}/atayanc_plot_dsm_1m_6.50.tif'
with rasterio.open(outt, 'w', **prof) as dd:
    dd.write(dsm_i16, 1)
json.dump({'bounds': [x0, y0, x1, y1], 'sigma_m': SIGMA, 'ksoft': KSOFT,
           'n_marks': int(inb.sum()), 'corr_bias': float(d_i.mean())},
          open(f'{OUTD}/dsm_meta_6.50.json', 'w'))
print('ГОТОВО 6.50d:', outt, (H1, W1))
