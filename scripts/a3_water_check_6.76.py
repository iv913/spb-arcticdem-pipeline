#!/usr/bin/env python3
"""6.76/A3 — контроль маски 6.76: критерий, отметки, сравнение с 6.22.

Критерий (перекалиброванный, A3): в маске нет связных компонент поверхности
>=1.5 м (conf=data) площадью >= 50 м2. Спекл-остаток (< 50 м2) считается
и перечисляется. Плюс: отметки каталога/confirmed в воде (регрессия от 6.22:
19->2 и 1), площадь по классам.
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
W76 = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.76.geojson'
FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
POOL2 = REPO / 'results' / 'atayanc_pool_v2' / 'atayanc_marks_pool_v2.csv'
CAT = REPO / 'results' / 'atayanc_6.50' / 'marks_catalog_6.50.csv'
OUTD = REPO / 'results' / 'water_a3_6.76'

DRY_CM = 150
MIN_COMP_M2 = 50.0
TILE_BIG = 0.05
DEG2_M2 = 111320.0 * 111132.0
t0 = time.time()
log = lambda m: print(f'[{time.time()-t0:5.0f}s] {m}', flush=True)

feats = json.load(open(W76))['features']
geoms = [shape(f['geometry']).buffer(0) for f in feats]
F = rasterio.open(FUS)
C = rasterio.open(CONF)
px_area = abs(F.transform.a) * 111320 * abs(F.transform.e) * 111133
log(f'feats {len(feats)}')

n_water = n_dry = n_fill = 0
big_comps = []
for fi, g in enumerate(geoms):
    minx, miny, maxx, maxy = g.bounds
    wins = []
    if (maxx - minx) <= TILE_BIG and (maxy - miny) <= TILE_BIG:
        wins = [(g, from_bounds(minx, miny, maxx, maxy, F.transform))]
    else:
        for x0 in np.arange(minx, maxx, TILE_BIG):
            for y0 in np.arange(miny, maxy, TILE_BIG):
                sub = g.intersection(box(x0, y0, x0 + TILE_BIG,
                                         y0 + TILE_BIG))
                if not sub.is_empty:
                    wins.append((sub, from_bounds(x0, y0 - TILE_BIG,
                                                  x0 + TILE_BIG,
                                                  y0 + TILE_BIG,
                                                  F.transform)))
    for sub, win0 in wins:
        win = win0.round_offsets().round_lengths()
        x0i, y0i, w_, h_ = (int(win.col_off), int(win.row_off),
                            int(win.width), int(win.height))
        if (w_ <= 0 or h_ <= 0 or x0i < 0 or y0i < 0
                or x0i + w_ > F.width or y0i + h_ > F.height):
            continue
        tr = F.window_transform(win)
        wm = rasterize([(sub, 1)], out_shape=(h_, w_), transform=tr, fill=0,
                       dtype='uint8')
        if not wm.any():
            continue
        fu = F.read(1, window=Window(x0i, y0i, w_, h_)).astype(np.int32)
        cf = C.read(1, window=Window(x0i, y0i, w_, h_))
        water = wm == 1
        fin = water & (fu != -32768)
        dry = fin & (fu >= DRY_CM) & (cf >= 10)
        n_water += int(water.sum())
        n_dry += int(dry.sum())
        n_fill += int((fin & (fu >= DRY_CM) & (cf >= 1) & (cf <= 4)).sum())
        if dry.any():
            lab, nl = ndimage.label(dry)
            cnts = np.bincount(lab.ravel(), minlength=nl + 1)
            big = np.where(cnts[1:] * px_area >= MIN_COMP_M2)[0] + 1
            for info, val in rio_shapes(np.isin(lab, big).astype(np.uint8),
                                        mask=np.isin(lab, big), transform=tr,
                                        connectivity=4):
                if val == 1:
                    p = shape(info)
                    if p.area > 0:
                        big_comps.append({'feat': fi,
                                          'area_m2': round(p.area * DEG2_M2),
                                          'lon': round(p.centroid.x, 5),
                                          'lat': round(p.centroid.y, 5)})
    if fi % 200 == 0:
        log(f'feat {fi}: вода {n_water*px_area/1e6:.2f} км2, dry '
            f'{n_dry*px_area/1e6:.3f} км2, больших компонент '
            f'{len(big_comps)}')

log(f'КРИТЕРИЙ: вода {n_water*px_area/1e6:.2f} км2; dry-пиксели (>=1.5 м, '
    f'data) {n_dry*px_area/1e6:.3f} км2 ({n_dry} px); компонент >=50 м2: '
    f'{len(big_comps)} (сумма {sum(c["area_m2"] for c in big_comps)/1e6:.3f} '
    f'км2); dry-in-fill {n_fill*px_area/1e6:.2f} км2 (не улика)')

# отметки в воде
geoms_prepped = geoms
from shapely.vectorized import contains as vcontains


def marks_in_water(path, use_filter=None):
    n = 0
    hits = []
    for r in csv.DictReader(open(path)):
        if use_filter and r['use'] not in use_filter:
            continue
        lo, la = float(r['lon']), float(r['lat'])
        for g in geoms_prepped:
            if g.contains(__import__('shapely.geometry', fromlist=['Point'])
                          .Point(lo, la)):
                n += 1
                hits.append(r['id'])
                break
    return n, hits


n_cat, h_cat = marks_in_water(CAT)
n_conf, h_conf = marks_in_water(POOL2, ('ground_truth', 'ground_truth_0.1'))
print(f'отметки каталога в воде 6.76: {n_cat} {h_cat[:6]}')
print(f'подтверждённые (188) в воде 6.76: {n_conf} {h_conf}')

(OUTD / 'check_676_summary.json').write_text(json.dumps({
    'mask': 'inland_water_6.76',
    'water_km2': round(n_water * px_area / 1e6, 2),
    'dry_data_km2': round(n_dry * px_area / 1e6, 4),
    'dry_data_px': n_dry,
    'components_ge_50m2': len(big_comps),
    'components_area_km2': round(sum(c['area_m2'] for c in big_comps) / 1e6,
                                 4),
    'dry_in_fill_km2': round(n_fill * px_area / 1e6, 2),
    'marks_catalog_in_water': n_cat,
    'marks_confirmed_in_water': n_conf,
    'big_components': sorted(big_comps, key=lambda c: -c['area_m2'])[:20],
}, ensure_ascii=False, indent=1), encoding='utf-8')
log('OK')
