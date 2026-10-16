#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.53c — РЕВИЗИЯ v2 участка: ступенька уреза ВМЕСТО барьера.

Диагноз 6.53b: рампа fusion у воды широка — p50 base растёт 1.99 (0-2 м) ->
3.44 (~80 м), p25 пересекает минимум гранита 2.26 м на ~50 м. Барьер 12 м
(6.52) оставляет ленты 18-40 м — обведены пользователем на +1 м (Биржевая
пл. 3 полигона 3634 м2, Университетская наб. 4 полигона 3867 м2).

Фикс (честная физика вместо отсечки): в полосе dist<50 м от воды высота
восстанавливается ЗНАЧЕНИЕМ БЛИЖАЙШЕГО ЯКОРЯ: отметки каталога 6.50 (все,
они сняты с планов и стоят на стенках набережных/пирсов) + пиксели суши
dist>=50 м (шаг 4 м). Якоря-отметки дают стенку набережной её реальную
высоту -> +1/+2 у набережных сухо (гранит 2.26-4.60), +3/+5 затопление
Стрелки (2.74-2.90) сохраняется. Якорь дальше 120 м -> высота не меняется.

Затопление: (dsm3 <= L) & суша & dist>=2 м (страховка от кромки OSM-воды),
компоненты >= 300 м2. Уровни 0.5, 1, 1.5, 2, 2.5, 3, 4, 5 (шаг 0.5 — по
красной обводке селектора на скриншоте пользователя).

Выход: results/atayanc_6.53/atayanc_plot_dsm_1m_6.53.tif
       results/atayanc_6.53/flood_plot_6.53.geojson + dsm_meta_6.53.json
"""
import json, csv, os
import numpy as np
import rasterio
from rasterio.transform import Affine as RAff
from rasterio.features import rasterize, shapes as rio_shapes
from rasterio.warp import reproject, Resampling
from shapely.geometry import shape, Polygon, mapping, box
from shapely.ops import unary_union
from scipy.spatial import cKDTree
from scipy import ndimage

OUTD = '/tmp/my-project/results/atayanc_6.53'
os.makedirs(OUTD, exist_ok=True)
FUSE = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CAT = '/home/z/my-project/download/atayanc_6.50/marks_catalog_6.50.csv'
GEO = '/home/z/my-project/download/atayanc_6.30/georef_6.30.json'
B30 = '/home/z/my-project/download/atayanc_6.30/plan_buildings_6.30.geojson'
PB = [30.283288502978362, 59.93759035347949, 30.325441520283814, 59.952163132920404]

geo = json.load(open(GEO)); A = RAff(*geo['affine_deg'])
SIGMA = 40.0; KSOFT = 0.35; CAP = 1.5
CONF_W = {'H': 1.0, 'M': 0.7, 'L': 0.45}
BAND = 50.0     # ширина полосы восстановления (6.53b: p25 < 2.26 до ~50 м)
FALLBACK = 120.0  # дальше якоря не тянуть
BAR = 2.0       # страховка от кромки OSM-воды
MINP = 300      # м2, минимальная компонента

# ---------- окно fusion -> base 1 м (как 6.52b)
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
print('base 1 м: %s, nan %d' % (str(base.shape), np.isnan(base).sum()))

# ---------- отметки
rows = list(csv.DictReader(open(CAT, encoding='utf-8')))
mx = np.array([float(r['lon']) for r in rows]); my = np.array([float(r['lat']) for r in rows])
mv = np.array([float(r['h_bs77_m']) for r in rows])
mc = np.array([CONF_W[r['conf']] for r in rows])
px_x = (mx - x0) * MLON; px_y = (y1 - my) * MLAT
inb = (px_x >= 0) & (px_x < W1) & (px_y >= 0) & (px_y < H1)
r_idx = np.clip(px_y[inb].astype(int), 0, H1 - 1)
c_idx = np.clip(px_x[inb].astype(int), 0, W1 - 1)

# ref p25 9x9 по суше + коррекция (без изменений от 6.52b)
ref = np.full(int(inb.sum()), np.nan)
for i, (r, c) in enumerate(zip(r_idx, c_idx)):
    w = base[max(0, r - 4):min(H1, r + 5), max(0, c - 4):min(W1, c + 5)]
    wv = w[~np.isnan(w) & (w >= 0.5)]
    if len(wv): ref[i] = np.percentile(wv, 25)
valid = ~np.isnan(ref)
d_i = mv[inb][valid] - ref[valid]
pts_m = np.stack([px_x[inb][valid], px_y[inb][valid]], axis=1)
w_i = mc[inb][valid]
print('diff (land-only ref): bias %+.2f, MAD %.2f, n=%d' %
      (d_i.mean(), np.median(np.abs(d_i)), valid.sum()))

def w_median(vals, wts):
    o = np.argsort(vals); v, w = vals[o], wts[o]
    cw = np.cumsum(w)
    return float(v[np.searchsorted(cw, 0.5 * cw[-1])])

tree = cKDTree(pts_m); RMAX = SIGMA * 3.0; KNN = 24
def neigh(i):
    dist, idx = tree.query(pts_m[i], k=min(KNN, len(pts_m)), distance_upper_bound=RMAX)
    dist = np.atleast_1d(dist); idx = np.atleast_1d(idx)
    wgt = np.exp(-(dist ** 2) / (2 * SIGMA ** 2)) * w_i[np.clip(idx, 0, len(w_i) - 1)]
    ok = idx < len(pts_m)
    return dist[ok], idx[ok], wgt[ok]

med_i = np.zeros(len(pts_m))
for i in range(len(pts_m)):
    _, idx, wgt = neigh(i)
    med_i[i] = w_median(d_i[idx], wgt) if len(idx) >= 3 else d_i[i]
resid = d_i - med_i
mad_r = np.median(np.abs(resid - np.median(resid))) * 1.4826
thr = max(0.75, 2.5 * mad_r)
inl = np.abs(resid) <= thr
print('ПРОХОД 1: выбросов %d из %d (порог %.2f)' % (int((~inl).sum()), len(inl), thr))

d_use0 = d_i[inl].copy()
# 6.53: cap НЕ применяем (143/318 |d_i|>1.5 — это систематика fusion, не выбросы;
# выбросы уже отброшены проходом 1). Локализация провалов поля — через SIGMA 40.
d_use = d_use0
p_use = pts_m[inl]; w_use = w_i[inl]
print('CAP отключен: d_i инлаеров p5 %+.2f p95 %+.2f' % (np.percentile(d_use, 5), np.percentile(d_use, 95)))
tree2 = cKDTree(p_use); GS = 4
WG, HG = (W1 + GS - 1) // GS, (H1 + GS - 1) // GS
gx = np.arange(WG) * GS + GS / 2; gy = np.arange(HG) * GS + GS / 2
GX, GY = np.meshgrid(gx, gy)
Gpts = np.stack([GX.ravel(), GY.ravel()], axis=1)
corr_g = np.zeros(len(Gpts), np.float32); beta_g = np.zeros(len(Gpts), np.float32)
CHUNK = 200000
for s in range(0, len(Gpts), CHUNK):
    chunk = Gpts[s:s + CHUNK]
    dist, idx = tree2.query(chunk, k=min(24, len(p_use)), distance_upper_bound=RMAX)
    dist = np.atleast_2d(dist); idx = np.atleast_2d(idx)
    wgt = np.exp(-(dist ** 2) / (2 * SIGMA ** 2)) * w_use[np.clip(idx, 0, len(w_use) - 1)]
    bad = idx >= len(p_use); wgt[bad] = 0.0
    sw = wgt.sum(axis=1); okc = sw > 1e-9
    corr_g[s:s + CHUNK] = np.where(okc, (wgt * d_use[np.clip(idx, 0, len(d_use) - 1)]).sum(axis=1)
                                   / np.maximum(sw, 1e-9), 0.0)
    beta_g[s:s + CHUNK] = sw / (sw + KSOFT)
corr_fb = corr_g.reshape(HG, WG) * beta_g.reshape(HG, WG) * (beta_g.reshape(HG, WG) > 0.05)
ground2 = np.zeros((H1, W1), np.float32)
reproject(corr_fb.astype(np.float32), ground2,
          src_transform=RAff(GS / MLON, 0, x0, 0, -GS / MLAT, y1),
          src_crs='EPSG:4326', dst_transform=aff1, dst_crs='EPSG:4326',
          resampling=Resampling.bilinear)
ground = base + ground2
ground[np.isnan(base)] = np.nan
np.save(f'{OUTD}/corr_field_653.npy', ground2)  # диагностика провалов
with open(f'{OUTD}/marks_di_653.csv', 'w') as fm:
    fm.write('id,lon,lat,h,ref,d_i,conf,inlier\n')
    for j in range(len(pts_m)):
        jl = np.where(inb)[0][valid][j]
        mid = rows[jl].get('id', '?')
        fm.write('%s,%s,%s,%.2f,%.2f,%+.2f,%s,%d\n' % (
            mid, rows[jl]['lon'], rows[jl]['lat'], mv[inb][valid][j],
            ref[valid][j], d_i[j], rows[jl]['conf'], int(inl[j])))
gi = np.stack([r_idx, c_idx]); gvals = ground[gi[0], gi[1]]
gd = mv[inb] - gvals; okg = ~np.isnan(gvals)
print('контроль ground vs отметки: bias %+.2f, MAD %.2f (n=%d)' %
      (np.nanmean(gd[okg]), np.nanmedian(np.abs(gd[okg])), int(okg.sum())))

# ---------- вода / дистанция
water_g = unary_union([shape(f['geometry']).buffer(0) for f in
                       json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))['features']])
BB = Polygon([(PB[0], PB[1]), (PB[2], PB[1]), (PB[2], PB[3]), (PB[0], PB[3])])
land = BB.difference(water_g)
land_m = rasterize([(mapping(land), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
wmask = rasterize([(mapping(water_g.intersection(BB)), 1)], out_shape=(H1, W1),
                  transform=aff1).astype(bool)
dist_w = ndimage.distance_transform_edt(~wmask)  # м/px

# ---------- СТУПЕНЬКА: восстановление полосы dist<BAND ближайшим якорем
# якоря: отметки (pts + mv) + пиксели суши dist>=BAND (шаг 4, finite ground)
band_m = land_m & (dist_w < BAND) & np.isfinite(ground)
print('полоса восстановления: %d px (%.2f км2)' % (band_m.sum(), band_m.sum() / 1e6))
reliable = land_m & (dist_w >= BAND) & np.isfinite(ground)
ry, rx = np.where(reliable)
ry = ry[::4]; rx = rx[::4]
# 1 px = 1 м (aff1 = 1/MLON deg/px), метрическое пространство == пиксельное
a_px = np.stack([rx, ry], axis=1).astype(np.float64)
a_val_px = ground[ry, rx]
a_pts = np.vstack([pts_m, a_px])
a_val = np.concatenate([mv[inb][valid], a_val_px])
a_is_mark = np.concatenate([np.ones(int(valid.sum()), bool), np.zeros(len(ry), bool)])
print('якорей: отметок %d + пикселей %d' % (int(valid.sum()), len(ry)))
tree_a = cKDTree(a_pts)
by, bx = np.where(band_m)
b_pts = np.stack([bx, by], axis=1).astype(np.float64)
dd, ii = tree_a.query(b_pts, k=1, distance_upper_bound=FALLBACK)
ok = np.isfinite(dd) & (dd <= FALLBACK)
rest = np.full(len(b_pts), np.nan)
rest[ok] = a_val[ii[ok]]
src_mark = np.zeros(len(b_pts), bool)
src_mark[ok] = a_is_mark[ii[ok]]
ground3 = ground.copy()
ground3[by[ok], bx[ok]] = rest[ok]
n_rest = int(ok.sum()); n_mark = int(src_mark.sum())
print('восстановлено %d px (%.0f%% полосы), из них по отметке %.0f%%; без якоря %d px' %
      (n_rest, 100 * n_rest / max(band_m.sum(), 1), 100 * n_mark / max(n_rest, 1),
       int((~ok).sum())))

# валидация ступеньки на отметках в полосе
gb3 = ground3[np.clip(r_idx, 0, H1 - 1), np.clip(c_idx, 0, W1 - 1)]
near_b = (dist_w[np.clip(r_idx, 0, H1 - 1), np.clip(c_idx, 0, W1 - 1)] < BAND) & okg & np.isfinite(gb3)
if near_b.any():
    print('отметки в полосе (n=%d): до bias %+.2f/MAD %.2f -> после bias %+.2f/MAD %.2f' % (
        int(near_b.sum()), np.nanmean(gd[near_b]), np.nanmedian(np.abs(gd[near_b])),
        np.nanmean(gb3[near_b] - mv[inb][near_b]), np.nanmedian(np.abs(gb3[near_b] - mv[inb][near_b]))))

# ---------- крыши (как 6.52b, поверх ground3)
bfc = json.load(open(B30))
shapes_roof, vals_roof = [], []
valid_m = ~np.isnan(ground3)
for ft in bfc['features']:
    h = ft['properties'].get('h')
    if h is None: continue
    g = shape(ft['geometry'])
    ring = g.buffer(18).difference(g.buffer(6))
    msk = rasterize([(mapping(ring), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
    if msk.sum() < 20:
        msk = rasterize([(mapping(g.buffer(25)), 1)], out_shape=(H1, W1), transform=aff1).astype(bool)
    gv = ground3[msk & valid_m]
    if len(gv) < 10: continue
    shapes_roof.append(mapping(g)); vals_roof.append(float(np.median(gv) + h))
print('крыш: %d' % len(shapes_roof))
dsm = ground3.copy()
if shapes_roof:
    roof_r = rasterize(list(zip(shapes_roof, vals_roof)), out_shape=(H1, W1),
                       transform=aff1, fill=0, dtype='float32')
    have = (roof_r > 0) & valid_m
    dsm[have] = np.maximum(dsm[have], roof_r[have])
water = (base < 0.25) | np.isnan(base)
dsm[water] = np.nan
print('DSM v3: min %.2f max %.2f' % (np.nanmin(dsm), np.nanmax(dsm)))

dsm_filled = np.where(np.isfinite(dsm), np.clip(dsm, -100, 900), -327.68)
dsm_i16 = np.round(dsm_filled * 100).astype(np.int16)
prof = dict(driver='GTiff', width=W1, height=H1, count=1, dtype='int16',
            crs='EPSG:4326', transform=aff1, compress='deflate', nodata=-32768)
with rasterio.open(f'{OUTD}/atayanc_plot_dsm_1m_6.53.tif', 'w', **prof) as dd:
    dd.write(dsm_i16, 1)
json.dump({'bounds': [x0, y0, x1, y1], 'sigma_m': SIGMA, 'cap': CAP, 'knn': 24,
           'ref': 'p25 land-only', 'n_marks': int(inb.sum()),
           'band_m': BAND, 'fallback_m': FALLBACK, 'bar_m': BAR,
           'restored_px': n_rest, 'restored_by_mark': n_mark},
          open(f'{OUTD}/dsm_meta_6.53.json', 'w'))
print('TIF готов')

# ---------- затопление: уровни 0.5-5, барьер только 2 м
LEVELS = (0.5, 1, 1.5, 2, 2.5, 3, 4, 5)
floods = {}
for lv in LEVELS:
    m = (dsm <= lv) & land_m & (dist_w >= BAR) & np.isfinite(dsm)
    lab, n = ndimage.label(m)
    if n:
        sizes = ndimage.sum(m, lab, range(1, n + 1))
        keep = np.zeros(n + 1, bool); keep[1:] = sizes >= MINP
        m2 = keep[lab]
    else:
        m2 = m
    feats = []
    for g, v in rio_shapes(m2.astype(np.uint8), mask=m2, transform=aff1):
        p = shape(g)
        if p.is_empty or p.area < 2e-11: continue
        p = p.simplify(3e-5)
        if p.is_empty or p.geom_type != 'Polygon': continue
        feats.append({'type': 'Feature', 'properties': {'level': lv,
                      'area_m2': round(p.area * (MLON / 1) * MLAT, 0)},
                      'geometry': mapping(p)})
    floods[lv] = feats
    print('затопление +%.1f м: %d полигонов, %.3f км2' %
          (lv, len(feats), sum(f['properties']['area_m2'] for f in feats) / 1e6))
json.dump({str(k): v for k, v in floods.items()},
          open(f'{OUTD}/flood_plot_6.53.geojson', 'w'), ensure_ascii=False)

# ---------- ВАЛИДАЦИЯ
C2 = box(30.293, 59.9405, 30.312, 59.9465)
C3 = box(30.272, 59.9335, 30.303, 59.9425)
STRELKA = box(30.298, 59.940, 30.318, 59.9455)
print('\n=== ВАЛИДАЦИЯ 6.53 ===')
for cname, cb in (('C2 Биржевая', C2), ('C3 Университетская', C3), ('Стрелка', STRELKA)):
    row = []
    for lv in LEVELS:
        a = 0.0
        for f in floods.get(lv, []):
            g = shape(f['geometry'])
            if g.intersects(cb): a += g.area * MLON * MLAT
        row.append(f'+{lv}:{a:.0f}')
    print(f'{cname:20s} ' + ' '.join(row))
# доля +1 в 15 м от воды
f1 = unary_union([shape(f['geometry']) for f in floods[1]]) if floods[1] else None
if f1 is not None and not f1.is_empty:
    from shapely.geometry import Point
    rng = np.random.default_rng(11); pts = []
    minx, miny, maxx, maxy = f1.bounds
    while len(pts) < 800:
        p = Point(rng.uniform(minx, maxx), rng.uniform(miny, maxy))
        if f1.contains(p): pts.append((p.x, p.y))
    if pts:
        ds = np.array([water_g.distance(Point(*p)) * MLON for p in pts])
        print('+1 м в 15 м от воды: %.0f%% (медиана %.0f м, n=%d)' %
              (100 * (ds <= 15).mean(), np.median(ds), len(ds)))
    else:
        print('+1 м: полигонов нет (выборка пуста)')
else:
    print('+1 м: затоплений НЕТ')
print('ГОТОВО 6.53c ->', OUTD)
