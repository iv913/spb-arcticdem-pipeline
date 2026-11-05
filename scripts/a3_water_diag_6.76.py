#!/usr/bin/env python3
"""6.76/A3 — диагностика «сухих» пикселей внутри водной маски 6.22 (быстрая).

A3-критерий (datum_B3_6.65): «ни одного пикселя поверхности > 1.5 м внутри
водной маски». Шаг 1 — счёт и кластеризация улик:
  dry = water & (fusion >= 150 см) & (conf >= 10)   # улики = data
  dryfill = water & (fusion >= 150 см) & (conf 1..4) # fill — не улика
Статистика компонентов через ndimage (без повторного растеризатора).
Выход: results/water_a3_6.76/diag_components.csv, diag_summary.json,
diag_features.csv.
"""
import csv
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import rasterio
from rasterio.features import rasterize, shapes as rio_shapes
from rasterio.windows import Window, from_bounds
from scipy import ndimage
from shapely.geometry import shape, box

REPO = Path('/tmp/my-project/gh-clone')
W22 = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.22.geojson'
FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
OUTD = REPO / 'results' / 'water_a3_6.76'
OUTD.mkdir(parents=True, exist_ok=True)

DRY_CM = 150
TILE_BIG = 0.05
t0 = time.time()
log = lambda m: print(f'[{time.time()-t0:5.0f}s] {m}', flush=True)

feats = json.load(open(W22))['features']
geoms = [shape(f['geometry']).buffer(0) for f in feats]
F = rasterio.open(FUS)
C = rasterio.open(CONF)
px_area = abs(F.transform.a) * 111320 * abs(F.transform.e) * 111133
log(f'feats {len(geoms)}, grid {F.width}x{F.height}, px_area {px_area:.1f} m2')

all_comps = []
fstat = []
tot = Counter()


def process_window(fi, sub, win):
    x0i, y0i = int(win.col_off), int(win.row_off)
    w_, h_ = int(win.width), int(win.height)
    if (w_ <= 0 or h_ <= 0 or x0i < 0 or y0i < 0
            or x0i + w_ > F.width or y0i + h_ > F.height):
        return 0, 0, 0
    tr = F.window_transform(win)
    wm = rasterize([(sub, 1)], out_shape=(h_, w_), transform=tr, fill=0,
                   dtype='uint8')
    if not wm.any():
        return 0, 0, 0
    fu = F.read(1, window=Window(x0i, y0i, w_, h_)).astype(np.int32)
    cf = C.read(1, window=Window(x0i, y0i, w_, h_))
    water = wm == 1
    fin = water & (fu != -32768)
    dry = fin & (fu >= DRY_CM) & (cf >= 10)
    n_water = int(water.sum())
    n_dry = int(dry.sum())
    n_fill = int((fin & (fu >= DRY_CM) & (cf >= 1) & (cf <= 4)).sum())
    if not dry.any():
        return n_water, n_dry, n_fill
    lab, nl = ndimage.label(dry)
    if not nl:
        return n_water, n_dry, n_fill
    # статистики компонентов одним проходом
    flat = lab.ravel()
    cnts = np.bincount(flat, minlength=nl + 1)
    sums = np.bincount(flat, weights=fu.ravel().astype(np.float64),
                       minlength=nl + 1)
    mins = ndimage.minimum(fu, lab, index=np.arange(1, nl + 1))
    maxs = ndimage.maximum(fu, lab, index=np.arange(1, nl + 1))
    confs = np.bincount(flat, weights=(cf >= 10).ravel().astype(np.float64),
                        minlength=nl + 1)
    areas = cnts[1:] * px_area
    for info, val in rio_shapes(lab, mask=dry, transform=tr, connectivity=4):
        if val == 0:
            continue
        v = int(val)
        p = shape(info)
        if p.area <= 0:
            continue
        c = p.centroid
        all_comps.append({
            'feat': fi, 'lon': round(c.x, 6), 'lat': round(c.y, 6),
            'area_m2': round(float(areas[v - 1]), 0),
            'n_px': int(cnts[v]),
            'z_min_m': round(float(mins[v - 1]) / 100, 2),
            'z_med_m': round(float(sums[v] / cnts[v]) / 100, 2),
            'z_max_m': round(float(maxs[v - 1]) / 100, 2),
            'conf_data_frac': round(float(confs[v] / cnts[v]), 3)})
    return n_water, n_dry, n_fill


for fi, g in enumerate(geoms):
    minx, miny, maxx, maxy = g.bounds
    n_water = n_dry = n_fill = 0
    if (maxx - minx) <= TILE_BIG and (maxy - miny) <= TILE_BIG:
        win = from_bounds(minx, miny, maxx, maxy, F.transform)
        win = win.round_offsets().round_lengths()
        nw, nd, nf = process_window(fi, g, win)
        n_water, n_dry, n_fill = nw, nd, nf
    else:
        for x0 in np.arange(minx, maxx, TILE_BIG):
            for y0 in np.arange(miny, maxy, TILE_BIG):
                sub = g.intersection(box(x0, y0, x0 + TILE_BIG,
                                         y0 + TILE_BIG))
                if sub.is_empty:
                    continue
                win = from_bounds(x0, y0 - TILE_BIG, x0 + TILE_BIG,
                                  y0 + TILE_BIG, F.transform)
                win = win.round_offsets().round_lengths()
                nw, nd, nf = process_window(fi, sub, win)
                n_water += nw
                n_dry += nd
                n_fill += nf
    tot['water_px'] += n_water
    tot['dry_px'] += n_dry
    tot['dryfill_px'] += n_fill
    fstat.append({'feat': fi, 'water_px': n_water, 'dry_px': n_dry,
                  'raw_comps': sum(1 for c in all_comps if c['feat'] == fi),
                  'water_km2': round(n_water * px_area / 1e6, 2),
                  'dry_km2': round(n_dry * px_area / 1e6, 3)})
    if fi % 300 == 0:
        log(f'feat {fi}: вода {tot["water_px"]*px_area/1e6:.1f} км2, '
            f'сухих {tot["dry_px"]*px_area/1e6:.2f} км2')

log(f'ИТОГО: вода {tot["water_px"]*px_area/1e6:.2f} км2; сухих (data) '
    f'{tot["dry_px"]*px_area/1e6:.2f} км2 ({tot["dry_px"]} px, '
    f'{tot["dry_px"]*px_area/1e4:.0f} га); dry-in-fill '
    f'{tot["dryfill_px"]*px_area/1e6:.2f} км2; компонентов {len(all_comps)}')

with open(OUTD / 'diag_components.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=['feat', 'lon', 'lat', 'area_m2',
                                       'n_px', 'z_min_m', 'z_med_m',
                                       'z_max_m', 'conf_data_frac'])
    w.writeheader()
    w.writerows(all_comps)

top = sorted(all_comps, key=lambda r: -r['area_m2'])[:25]
summary = {
    'date': '2026-11-05', 'stage': '6.76/A3 diag',
    'criterion': '0 px of surface >1.5 m inside water mask',
    'mask': 'inland_water_6.22', 'surface': 'fusion 6.00 A2 (D=+0.004)',
    'evidence_rule': 'fusion>=150cm AND conf>=10 (data)',
    'px_area_m2': round(px_area, 2),
    'totals': {'water_km2': round(tot['water_px'] * px_area / 1e6, 2),
               'dry_data_km2': round(tot['dry_px'] * px_area / 1e6, 2),
               'dry_data_px': tot['dry_px'],
               'dry_in_fill_km2': round(tot['dryfill_px'] * px_area / 1e6, 2),
               'raw_components': len(all_comps)},
    'top_components': top,
    'features_with_dry': sum(1 for f in fstat if f['dry_px'] > 0),
}
(OUTD / 'diag_summary.json').write_text(
    json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
with open(OUTD / 'diag_features.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(fstat[0].keys()))
    w.writeheader()
    w.writerows(fstat)

print('\nТОП-20 компонентов по площади:')
for r in top[:20]:
    print(f"  feat{r['feat']:4d} ({r['lon']:.4f},{r['lat']:.4f}) "
          f"{r['area_m2']/1e4:7.1f} га z {r['z_min_m']:.2f}..{r['z_max_m']:.2f}"
          f" data {r['conf_data_frac']:.2f}")
log('OK')
