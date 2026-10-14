#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.00 — МЕГА-СШИВКА: единая высотная поверхность СПб из всех источников.

Все источники уже перепроверены и отфильтрованы (этапы 5.66-5.96). После всех
фильтров сшиваем их в ОДНУ поверхность, ДОСТРАИВАЯ отсутствующие элементы,
с 4 степенями допущения (дальность достройки от измеренных данных).

Алгоритм:
  1) База = гибридная мозаика 5.90 (2 м, см, БС-77) -> сетка ~8 м (кэш 5.96).
  2) Лакуны (nodata) достраиваются мультисеточным решением Лапласа
     (coarse-to-fine diffusion от валидных данных).
  3) Степени допущения: A1 <=250 м, A2 <=1 км, A3 <=3 км, A4 вся сетка.
  4) Якорение: в лакунами поверхность притягивается к независимым точкам
     (ATL06 canon v4 h_baltic + отметки, IDW-остатки, демпф 0.6).
  5) Валидация: holdout 50% canon-точек в лакунах — MAD/med/p95 per A.
  6) Зоны затопления достроенных частей: bathtub как 5.96 per (A, уровень).
  7) Выход: растр A2 2 м + confidence, GeoJSON fill-зон, отчёты.

Выход:
  gh-clone/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif  (Int16, см, БС-77)
  gh-clone/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif    (uint8 классы)
  results/fusion_6.00/fill_zones_6.00_A{1..4}.geojson
  results/fusion_6.00/fusion_report_6.00.json
"""
import gc
import csv
import gzip
import json
import math
import os
import time

import numpy as np
import rasterio
from rasterio import features
from scipy import ndimage

CACHE = '/tmp/my-project/results/flood_zones_5.96/grid_cache_5.96.npz'
MOSAIC = '/tmp/my-project/gh-clone/download/mosaic_5.90/spb_hybrid_2m_mosaic_5.90.tif'
MCLF = '/tmp/my-project/gh-clone/download/mclf_v3/mclf_v3_mask_5.90.tif'
CANON = '/tmp/my-project/gh-clone/scripts/dem_cache/densify_canon_5.93/unified.csv.gz'
MARKS = '/tmp/my-project/gh-clone/results/marks_val_5.95.csv'
OUTD = '/tmp/my-project/results/fusion_6.00'
TIFD = '/tmp/my-project/gh-clone/download/mosaic_6.00'

H_WATER = 0.70
MIN_PART_CELLS = 300          # ~1.9 га
SIMPLIFY_DEG = 0.00025        # ~25 м
LEVELS = [1.0, 1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
A_DIST = {1: 250.0, 2: 1000.0, 3: 3000.0, 4: 1e9}
A_SIGMA = {1: 1.5, 2: 2.0, 3: 3.0, 4: 4.0}
ANCHOR_R = 2000.0
ANCHOR_DAMP = 0.6
CITY = (29.30, 59.60, 30.75, 60.25)
MY = 111132.95
MX0 = 111319.49 * math.cos(math.radians(59.95))
FX, FY = 8, 4                 # агрегация кэша 5.96 (px мозаики на ячейку)


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


# ------------------------------------------------------------------ pyramid
def block_mean(h, valid, k):
    ny, nx = h.shape
    ny2, nx2 = ny // k, nx // k
    hh = h[:ny2 * k, :nx2 * k].reshape(ny2, k, nx2, k).astype(np.float32)
    vv = valid[:ny2 * k, :nx2 * k].reshape(ny2, k, nx2, k)
    s = np.where(vv, hh, 0.0).sum(axis=(1, 3))
    c = vv.sum(axis=(1, 3), dtype=np.int32)
    out = np.where(c > 0, s / np.maximum(c, 1), 0.0).astype(np.float32)
    v = c >= max(1, int(0.25 * k * k))
    return out, v


def upsample_to(g, shape):
    """Блочное растяжение coarse-грида до точной формы (nearest, i -> i//4)."""
    rows = np.clip(np.arange(shape[0]) // 4, 0, g.shape[0] - 1)
    cols = np.clip(np.arange(shape[1]) // 4, 0, g.shape[1] - 1)
    return g[np.ix_(rows, cols)]


def diffuse(h, fixed, iters):
    for _ in range(iters):
        hp = np.pad(h, 1, mode='edge')
        s = (hp[:-2, 1:-1] + hp[2:, 1:-1] + hp[1:-1, :-2] + hp[1:-1, 2:]) * 0.25
        h[~fixed] = s[~fixed]
    return h


def build_fill(h8, valid8):
    log('пирамида и мультисеточная релаксация...')
    g1h, g1v = block_mean(h8, valid8, 4)     # ~32 м
    g2h, g2v = block_mean(g1h, g1v, 4)       # ~128 м
    g3h, g3v = block_mean(g2h, g2v, 4)       # ~512 м
    log(f'уровни: G0 {h8.shape}, G1 {g1h.shape}, G2 {g2h.shape}, G3 {g3h.shape}')

    ind = ndimage.distance_transform_edt(~g3v, return_distances=False,
                                         return_indices=True)
    g3 = np.where(g3v, g3h, g3h[tuple(ind)])
    g3 = diffuse(g3, g3v, 200)

    g2 = np.where(g2v, g2h, upsample_to(g3, g2h.shape))
    g2 = diffuse(g2, g2v, 300)

    g1 = np.where(g1v, g1h, upsample_to(g2, g1h.shape))
    g1 = diffuse(g1, g1v, 300)

    g0 = np.where(valid8, h8, upsample_to(g1, h8.shape))
    log('финальная релаксация на сетке ~8 м (300 итераций)...')
    g0 = diffuse(g0, valid8, 300)
    return g0


# ------------------------------------------------------------------ anchors
def load_points():
    lo, la, zz, mk = [], [], [], []
    with gzip.open(CANON, 'rt') as f:
        rd = csv.DictReader(f)
        for r in rd:
            a, o = float(r['lat']), float(r['lon'])
            if not (CITY[0] <= o <= CITY[2] and CITY[1] <= a <= CITY[3]):
                continue
            lo.append(o); la.append(a); zz.append(float(r['h_baltic'])); mk.append(0)
    n_canon = len(lo)
    with open(MARKS) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                a, o, v = float(r['lat']), float(r['lon']), float(r['z'])
            except Exception:
                continue
            if not (CITY[0] <= o <= CITY[2] and CITY[1] <= a <= CITY[3]):
                continue
            lo.append(o); la.append(a); zz.append(v); mk.append(1)
    log(f'точки: canon {n_canon}, отметки {len(lo) - n_canon}')
    return (np.array(lo), np.array(la), np.array(zz, dtype=np.float64),
            np.array(mk, dtype=bool), n_canon)


def grid_of(la, lo, tr, shape):
    c = np.clip(((np.asarray(lo) - tr.c) / tr.a).astype(np.int64), 0, shape[1] - 1)
    r = np.clip(((np.asarray(la) - tr.f) / tr.e).astype(np.int64), 0, shape[0] - 1)
    return r, c


def anchor_correction(g0, holes, tr, shape, lo, la, z, triage=None):
    """Остатки якорей в лакунах, накопление на сетке ~32 м (экономия RAM)."""
    r, c = grid_of(la, lo, tr, shape)
    inhole = holes[r, c]
    if triage is not None:
        inhole = inhole & triage
    resid = z - g0[r, c].astype(np.float64)
    n_in = int(inhole.sum())
    if n_in:
        log(f'якорей в лакунах: {n_in}/{len(lo)}, med resid '
            f'{np.median(resid[inhole]):+.2f} м')
    else:
        log('якорей в лакунах: 0')
    ny1, nx1 = shape[0] // 4, shape[1] // 4          # G1 ~32 м
    r1 = np.clip(r[inhole] // 4, 0, ny1 - 1)
    c1 = np.clip(c[inhole] // 4, 0, nx1 - 1)
    flat = r1 * nx1 + c1
    sums = np.bincount(flat, weights=resid[inhole], minlength=ny1 * nx1)
    cnts = np.bincount(flat, minlength=ny1 * nx1)
    has = (cnts > 0).reshape(ny1, nx1)
    mean = (sums / np.maximum(cnts, 1)).reshape(ny1, nx1)
    n_cells = int(has.sum())
    if n_cells < 3:
        return np.zeros(shape, dtype=np.float32), 0
    ind = ndimage.distance_transform_edt(~has, return_distances=False,
                                         return_indices=True)
    rf = np.where(has, mean, mean[tuple(ind)]).astype(np.float32)
    rf = ndimage.gaussian_filter(rf, 1.5)             # ~48 м
    d1 = ndimage.distance_transform_edt(~has, sampling=(abs(tr.e) * MY * 4,
                                                        abs(tr.a) * MX0 * 4))
    d0 = upsample_to(d1.astype(np.float32), shape)
    corr = np.where(d0 <= ANCHOR_R, upsample_to(rf, shape) * ANCHOR_DAMP,
                    0.0).astype(np.float32)
    return corr, n_cells


# ------------------------------------------------------------------ polygons
def flood_polys(mask, area_row, grid_tr):
    lab, n = ndimage.label(mask, structure=np.ones((3, 3), dtype=np.int32))
    if n == 0:
        return 0.0, []
    sizes = np.bincount(lab.ravel())
    keep = np.where(sizes >= MIN_PART_CELLS)[0]
    keep = keep[keep != 0]
    if len(keep) == 0:
        return 0.0, []
    m = np.isin(lab, keep)
    area = float((area_row[:, None] * m).sum() / 1e6)
    geoms = [g for g, v in features.shapes(m.astype(np.uint8), mask=m,
                                           transform=grid_tr, connectivity=4)
             if v == 1]
    from shapely.geometry import shape as shp
    from shapely.ops import unary_union
    gs = [shp(g).simplify(SIMPLIFY_DEG, preserve_topology=True) for g in geoms]
    mp = unary_union(gs) if gs else None
    parts = []
    if mp is not None and not mp.is_empty:
        plist = [mp] if mp.geom_type == 'Polygon' else (
            list(mp.geoms) if mp.geom_type == 'MultiPolygon' else [])
        for g in plist:
            if g.geom_type != 'Polygon' or g.is_empty:
                continue
            rings = [[[round(x, 5), round(y, 5)] for x, y in g.exterior.coords]]
            rings += [[[round(x, 5), round(y, 5)] for x, y in rr.coords]
                      for rr in g.interiors]
            parts.append(rings)
    return area, parts


# ------------------------------------------------------------------ main
def main():
    os.makedirs(OUTD, exist_ok=True)
    os.makedirs(TIFD, exist_ok=True)
    t0 = time.time()
    z = np.load(CACHE)
    h8 = z['h8'].astype(np.float32)
    valid8 = z['valid8'].astype(bool)
    tr = rasterio.Affine(*z['tr'])
    ny, nx = h8.shape
    latc = tr.f + (np.arange(ny) + 0.5) * tr.e
    dx_m = abs(tr.a) * 111319.49 * np.cos(np.radians(latc))
    dy_m = abs(tr.e) * MY
    area_row = (dx_m * dy_m).astype(np.float64)
    cell_area = float(area_row.mean())
    log(f'сетка {ny}x{nx}, ячейка {dx_m.mean():.2f}x{dy_m:.2f} м, '
        f'валидно {valid8.mean()*100:.1f}%')

    if os.path.exists(os.path.join(OUTD, 'tmp_g0.npy')):
        log('кэш tmp_g0.npy найден — пропускаю релаксацию')
        g0 = np.load(os.path.join(OUTD, 'tmp_g0.npy'))
    else:
        g0 = build_fill(h8, valid8)
        np.save(os.path.join(OUTD, 'tmp_g0.npy'), g0)
    holes = ~valid8
    log(f'лакун: {holes.sum()*cell_area/1e6:.0f} км2')

    log('EDT дальности от данных...')
    dist = ndimage.distance_transform_edt(
        ~valid8, sampling=(abs(tr.e) * MY, abs(tr.a) * MX0)).astype(np.float32)

    lo_arr, la_arr, z_arr, mk_arr, n_canon = load_points()
    r_all, c_all = grid_of(la_arr, lo_arr, tr, (ny, nx))
    # триаж выбросов (деревья/крыша в периферийных ATL06): |z - fill| > 10 м
    resid0 = z_arr - g0[r_all, c_all].astype(np.float64)
    triage = np.abs(resid0) <= 10.0
    log(f'триаж 10 м: отброшено {int((~triage).sum())} точек из {len(lo_arr)}')
    corr, n_anchor_cells = anchor_correction(g0, holes, tr, (ny, nx),
                                             lo_arr, la_arr, z_arr, triage)

    # holdout: половина canon-точек в лакунах не участвует в якорении
    inhole_all = holes[r_all, c_all] & ~mk_arr & triage
    rng = np.random.default_rng(600)
    cand = np.where(inhole_all)[0]
    sel = rng.permutation(cand.size)
    hold_bool = np.zeros(len(lo_arr), dtype=bool)
    hold_bool[cand[sel[:cand.size // 2]]] = True
    log(f'holdout canon в лакунах: {int(hold_bool.sum())} '
        f'(якорная половина {int(cand.size - hold_bool.sum())})')

    report = {'stage': '6.00',
              'cell_m': [round(float(dx_m.mean()), 2), round(float(dy_m), 2)],
              'cell_area_m2': round(cell_area, 1), 'grid': [ny, nx],
              'valid_km2': round(valid8.sum() * cell_area / 1e6, 1),
              'holes_km2': round(holes.sum() * cell_area / 1e6, 1),
              'anchor_points_total': int(len(lo_arr)), 'canon_points': n_canon,
              'anchor_grid_cells': n_anchor_cells,
              'assumptions': {A: {'dist_m': A_DIST[A], 'sigma': A_SIGMA[A]}
                              for A in (1, 2, 3, 4)},
              'validation': {}}

    fill2 = None
    for A in (1, 2, 3, 4):
        tA = time.time()
        use = holes & (dist <= A_DIST[A])
        comb = np.where(valid8, h8, g0 + corr)
        sm = ndimage.gaussian_filter(comb, A_SIGMA[A])
        f = np.where(use, sm, np.nan).astype(np.float32)
        log(f'A{A}: достроено {use.sum()*cell_area/1e6:.1f} км2, '
            f'sigma={A_SIGMA[A]} ({time.time()-tA:.0f} с)')

        # валидация holdout canon (триаж) + отметки в достройке
        fh = f[r_all, c_all].astype(np.float64)
        ok = np.isfinite(fh)
        ho = ok & ~mk_arr & hold_bool & triage
        errs = z_arr[ho] - fh[ho]
        nmk = int((ok & mk_arr).sum())
        v = {'n_holdout': int(errs.size), 'marks_filled': nmk}
        if errs.size:
            v.update(mad=round(float(np.median(np.abs(errs - np.median(errs)))), 3),
                     med=round(float(np.median(errs)), 3),
                     p95=round(float(np.percentile(np.abs(errs), 95)), 3),
                     rmse=round(float(np.sqrt((errs ** 2).mean())), 3))
        report['validation'][f'A{A}'] = v
        log(f'   holdout n={v["n_holdout"]} MAD={v.get("mad")} p95={v.get("p95")}'
            f' | отметок в достройке: {nmk}')

        # зоны затопления достроенных частей
        feats = []
        for L in LEVELS:
            m = holes & np.isfinite(f) & (f < L) & (f > H_WATER)
            area, parts = flood_polys(m, area_row, tr)
            report.setdefault('fill_flood', {}).setdefault(f'A{A}', []).append(
                {'level_m': L, 'area_km2': round(area, 2)})
            if parts:
                feats.append({'type': 'Feature',
                              'properties': {'a': A, 'level_m': L,
                                             'area_km2': round(area, 2)},
                              'geometry': {'type': 'MultiPolygon',
                                           'coordinates': parts}})
            log(f'  A{A} +{L:.1f} м: {area:8.2f} км2 достроено')
        fc = {'type': 'FeatureCollection',
              'name': f'spb_fill_flood_A{A}_6.00', 'features': feats}
        with open(os.path.join(OUTD, f'fill_zones_6.00_A{A}.geojson'), 'w') as fh2:
            json.dump(fc, fh2, ensure_ascii=False)
        if A == 2:
            fill2 = f
        else:
            del f, sm, comb
        gc.collect()

    # отчёт и GeoJSON уже готовы — фиксируем ДО тяжёлой записи растров
    report['fill_flood'] = report.get('fill_flood', {})
    with open(os.path.join(OUTD, 'fusion_report_6.00.json'), 'w') as fh3:
        json.dump(report, fh3, ensure_ascii=False, indent=1)
    log('отчёт записан')

    del h8, valid8, corr
    gc.collect()
    write_rasters(fill2, dist)


def write_rasters(f8, dist):
    """2 м fused (A2, см) + conf (классы 0/1..4/10..13) блочно (мелкие блоки — RAM 3 ГБ)."""
    from rasterio.windows import Window
    with rasterio.open(MOSAIC) as sm0:
        W, H = sm0.width, sm0.height
        prof = sm0.profile.copy()
    prof.update(dtype='int16', nodata=-32768, compress='deflate', count=1)
    prof2 = prof.copy()
    prof2.update(dtype='uint8', nodata=0)
    out_t = os.path.join(TIFD, 'spb_terra_fusion_6.00_A2.tif')
    out_c = os.path.join(TIFD, 'spb_terra_conf_6.00_A2.tif')
    ny, nx = f8.shape
    with rasterio.open(out_t, 'w', **prof) as dt, \
         rasterio.open(out_c, 'w', **prof2) as dc, \
         rasterio.open(MOSAIC) as sm, \
         rasterio.open(MCLF) as smc:
        BH = 256
        for y0 in range(0, H, BH):
            h = min(BH, H - y0)
            win = Window(0, y0, W, h)
            a = sm.read(1, window=win)
            m = np.minimum(smc.read(1, window=win), 3)
            valid = a != -32768
            rows8 = np.clip((np.arange(y0, y0 + h) // FY), 0, ny - 1)
            cols8 = np.clip((np.arange(W) // FX), 0, nx - 1)
            fsub = f8[np.ix_(rows8, cols8)]
            dsub = dist[np.ix_(rows8, cols8)]
            fused = np.where(valid, a.astype(np.float32) * 0.01, np.nan)
            hole = (~valid) & np.isfinite(fsub)
            fused[hole] = fsub[hole]
            fi = np.where(np.isfinite(fused),
                          np.round(fused * 100).astype(np.int16), -32768)
            dt.write(fi, 1, window=win)
            conf = np.where(valid, (10 + m).astype(np.uint8), 0).astype(np.uint8)
            cls = np.select([dsub <= 250, dsub <= 1000, dsub <= 3000],
                            [1, 2, 3], default=4).astype(np.uint8)
            conf = np.where(hole, cls, conf).astype(np.uint8)
            dc.write(conf, 1, window=win)
    for p in (out_t, out_c):
        log(f'  {os.path.basename(p)}: {os.path.getsize(p)/1e6:.1f} MB')
    log('РАСТРЫ ГОТОВЫ')


if __name__ == '__main__':
    main()
