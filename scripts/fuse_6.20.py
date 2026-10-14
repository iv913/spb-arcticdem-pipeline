#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.20 — МЕГА-СШИВКА v2: якоря + полный набор DXF «Дачное» (2 326 точек).

Отличия от 6.00:
  1) В якоря добавлена DXF-съёмка Дачное (topo_v2.csv, col lat=5/lon=6/z=4):
     в 6.00 попадало лишь 34 дедуп-точки из marks_val_5.95.csv, из них
     16 прошли глобальный триаж 10 м (черновик Лапласа в SW-лакуне завышен
     на ~10 м — resid DXF med -10.5). Теперь полный набор.
  2) Триаж DXF — ЛОКАЛЬНЫЙ робастный (медиана окна 0.004° ≈ 440 м, |z-med|
     > 6 м -> отбраковка), глобальное правило 10 м к DXF не применяется.
  3) Валидация: holdout 50% DXF-точек (как canon) — MAD в лакунах per A.
  4) Растры НЕ переписываются: весь DXF-кластер дальше 3 км от данных
     (10.0-10.6 км) -> влияет только на A4-зоны; продукт A2 не меняется.

Выход: results/fusion_6.20/fill_zones_6.20_A{1..4}.geojson + fusion_report_6.20.json
"""
import gc
import csv
import gzip
import json
import math
import os
import time
from collections import defaultdict

import numpy as np
import rasterio
from rasterio import features
from scipy import ndimage

CACHE = '/tmp/my-project/results/flood_zones_5.96/grid_cache_5.96.npz'
CANON = '/tmp/my-project/gh-clone/scripts/dem_cache/densify_canon_5.93/unified.csv.gz'
MARKS = '/tmp/my-project/gh-clone/results/marks_val_5.95.csv'
DXF = '/tmp/my-project/gh-clone/scripts/data/topo_v2.csv'
OUTD = '/tmp/my-project/results/fusion_6.20'

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
DXF_TRIAGE_WIN = 0.004        # окно локальной медианы, град (~440 м)
DXF_TRIAGE_TOL = 6.0          # |z - localmedian| > tol -> отбраковка


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def dxf_local_triage(la, lo, z):
    """Медиана соседних бинов 0.002° (окно 3x3 ≈ 660 м) + допуск 6 м."""
    key_a = (la / 0.002).astype(int)
    key_o = (lo / 0.002).astype(int)
    bins = defaultdict(list)
    for j in range(len(la)):
        bins[(key_a[j], key_o[j])].append(j)
    lm = np.empty(len(la), dtype=np.float64)
    for (ka, ko), idxs in bins.items():
        neigh = []
        for da in (-1, 0, 1):
            for db in (-1, 0, 1):
                neigh += bins.get((ka + da, ko + db), [])
        lm[idxs] = np.median(z[neigh])
    keep = np.abs(z - lm) <= DXF_TRIAGE_TOL
    return keep, lm


def load_points():
    lo, la, zz, mk = [], [], [], []     # mk: 0=canon 1=marks 2=dxf
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
            if r['src'] == 'topo_dxf_dachnoe':      # полный набор ниже
                continue
            try:
                a, o, v = float(r['lat']), float(r['lon']), float(r['z'])
            except Exception:
                continue
            if not (CITY[0] <= o <= CITY[2] and CITY[1] <= a <= CITY[3]):
                continue
            lo.append(o); la.append(a); zz.append(v); mk.append(1)
    n_marks = len(lo) - n_canon
    with open(DXF) as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            try:
                a, o, v = float(r[5]), float(r[6]), float(r[4])
            except Exception:
                continue
            if not (CITY[0] <= o <= CITY[2] and CITY[1] <= a <= CITY[3]):
                continue
            lo.append(o); la.append(a); zz.append(v); mk.append(2)
    n_dxf = len(lo) - n_canon - n_marks
    log(f'точки: canon {n_canon}, отметки {n_marks}, DXF {n_dxf}')
    return (np.array(lo), np.array(la), np.array(zz, dtype=np.float64),
            np.array(mk, dtype=np.int8), n_canon, n_marks, n_dxf)


def grid_of(la, lo, tr, shape):
    c = np.clip(((np.asarray(lo) - tr.c) / tr.a).astype(np.int64), 0, shape[1] - 1)
    r = np.clip(((np.asarray(la) - tr.f) / tr.e).astype(np.int64), 0, shape[0] - 1)
    return r, c


def anchor_correction(g0, holes, tr, shape, lo, la, z, keep):
    """Остатки якорей в лакунах, накопление на сетке ~32 м (экономия RAM)."""
    r, c = grid_of(la, lo, tr, shape)
    inhole = holes[r, c] & keep
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


def upsample_to(g, shape):
    rows = np.clip(np.arange(shape[0]) // 4, 0, g.shape[0] - 1)
    cols = np.clip(np.arange(shape[1]) // 4, 0, g.shape[1] - 1)
    return g[np.ix_(rows, cols)]


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


def main():
    os.makedirs(OUTD, exist_ok=True)
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

    g0p = os.path.join(OUTD, 'tmp_g0.npy')
    if os.path.exists(g0p):
        log('кэш tmp_g0.npy найден — пропускаю релаксацию')
        g0 = np.load(g0p)
    else:
        src = '/tmp/my-project/results/fusion_6.00/tmp_g0.npy'
        if os.path.exists(src):
            log(f'переиспользую кэш 6.00: {src}')
            g0 = np.load(src)
            np.save(g0p, g0)
        else:
            log('НЕТ КЭША — нужен полный прогон 6.00!')
            return
    holes = ~valid8
    log(f'лакун: {holes.sum()*cell_area/1e6:.0f} км2')

    log('EDT дальности от данных...')
    dist = ndimage.distance_transform_edt(
        ~valid8, sampling=(abs(tr.e) * MY, abs(tr.a) * MX0)).astype(np.float32)

    lo_arr, la_arr, z_arr, mk_arr, n_canon, n_marks, n_dxf = load_points()
    r_all, c_all = grid_of(la_arr, lo_arr, tr, (ny, nx))
    d_all = dist[r_all, c_all]
    log(f'DXF: дист. до данных med {np.median(d_all[mk_arr==2])/1000:.1f} км '
        f'(все A4)')

    # триаж: canon/marks — глобальное 10 м; DXF — локальный медианный
    resid0 = z_arr - g0[r_all, c_all].astype(np.float64)
    triage = np.zeros(len(lo_arr), dtype=bool)
    gm = mk_arr != 2
    triage[gm] = np.abs(resid0[gm]) <= 10.0
    log(f'триаж 10 м (canon+отметки): отброшено {int((gm & ~triage).sum())} '
        f'из {int(gm.sum())}')
    di = np.where(mk_arr == 2)[0]
    keep_d, lmed = dxf_local_triage(la_arr[di], lo_arr[di], z_arr[di])
    triage[di] = keep_d
    log(f'триаж DXF (медиана {DXF_TRIAGE_WIN}°, ±{DXF_TRIAGE_TOL} м): '
        f'отброшено {int((~keep_d).sum())} из {len(di)}')

    corr, n_anchor_cells = anchor_correction(g0, holes, tr, (ny, nx),
                                             lo_arr, la_arr, z_arr, triage)

    # holdout: canon + DXF в лакунах (отметки всегда в якорях)
    inhole_all = holes[r_all, c_all] & (mk_arr != 1) & triage
    rng = np.random.default_rng(620)
    cand = np.where(inhole_all)[0]
    sel = rng.permutation(cand.size)
    hold_bool = np.zeros(len(lo_arr), dtype=bool)
    hold_bool[cand[sel[:cand.size // 2]]] = True
    n_hold_d = int((hold_bool & (mk_arr == 2)).sum())
    n_hold_c = int((hold_bool & (mk_arr == 0)).sum())
    log(f'holdout в лакунах: canon {n_hold_c}, DXF {n_hold_d}')

    report = {'stage': '6.20',
              'note': 'якоря + полный DXF Дачное (2326); растры A2 не меняются',
              'cell_m': [round(float(dx_m.mean()), 2), round(float(dy_m), 2)],
              'cell_area_m2': round(cell_area, 1), 'grid': [ny, nx],
              'valid_km2': round(valid8.sum() * cell_area / 1e6, 1),
              'holes_km2': round(holes.sum() * cell_area / 1e6, 1),
              'anchor_points_total': int(len(lo_arr)), 'canon_points': n_canon,
              'marks_points': n_marks, 'dxf_points': n_dxf,
              'anchor_grid_cells': n_anchor_cells,
              'dxf_triage': {'win_deg': DXF_TRIAGE_WIN, 'tol_m': DXF_TRIAGE_TOL,
                             'dropped': int((~keep_d).sum())},
              'assumptions': {A: {'dist_m': A_DIST[A], 'sigma': A_SIGMA[A]}
                              for A in (1, 2, 3, 4)},
              'validation': {}}

    for A in (1, 2, 3, 4):
        tA = time.time()
        use = holes & (dist <= A_DIST[A])
        comb = np.where(valid8, h8, g0 + corr)
        sm = ndimage.gaussian_filter(comb, A_SIGMA[A])
        f = np.where(use, sm, np.nan).astype(np.float32)
        log(f'A{A}: достроено {use.sum()*cell_area/1e6:.1f} км2, '
            f'sigma={A_SIGMA[A]} ({time.time()-tA:.0f} с)')

        fh = f[r_all, c_all].astype(np.float64)
        ok = np.isfinite(fh)
        ho = ok & hold_bool & triage
        errs = z_arr[ho] - fh[ho]
        ho_d = ho & (mk_arr == 2)
        errs_d = z_arr[ho_d] - fh[ho_d]
        v = {'n_holdout': int(errs.size), 'n_holdout_dxf': int(errs_d.size)}
        if errs.size:
            v.update(mad=round(float(np.median(np.abs(errs - np.median(errs)))), 3),
                     med=round(float(np.median(errs)), 3),
                     p95=round(float(np.percentile(np.abs(errs), 95)), 3),
                     rmse=round(float(np.sqrt((errs ** 2).mean())), 3))
        if errs_d.size:
            v['dxf'] = {'mad': round(float(np.median(np.abs(errs_d - np.median(errs_d)))), 3),
                        'med': round(float(np.median(errs_d)), 3),
                        'p95': round(float(np.percentile(np.abs(errs_d), 95)), 3)}
        report['validation'][f'A{A}'] = v
        log(f'   holdout n={v["n_holdout"]} MAD={v.get("mad")} | DXF n='
            f'{v["n_holdout_dxf"]} MAD={v.get("dxf", {}).get("mad")}')

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
              'name': f'spb_fill_flood_A{A}_6.20', 'features': feats}
        with open(os.path.join(OUTD, f'fill_zones_6.20_A{A}.geojson'), 'w') as fh2:
            json.dump(fc, fh2, ensure_ascii=False)
        del f, sm, comb
        gc.collect()

    with open(os.path.join(OUTD, 'fusion_report_6.20.json'), 'w') as fh3:
        json.dump(report, fh3, ensure_ascii=False, indent=1)
    log(f'отчёт записан ({time.time()-t0:.0f} с)')


if __name__ == '__main__':
    main()
