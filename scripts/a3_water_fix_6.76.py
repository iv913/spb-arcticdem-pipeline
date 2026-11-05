#!/usr/bin/env python3
"""6.76/A3 — пересборка колец воды: inland_water_6.22 -> 6.76.

Правило (улики канона, продолжение метода 6.62):
  dry = (fusion >= 150 см) & (conf >= 10 = data)  внутри полигона воды.
  Вода 6.76 = вода 6.22 минус ВСЕ связные dry-компоненты (пиксельно-точно,
  без буфера — улика и критерий на одной сетке 2 м).
  Фича с остатком воды < 100 м2 удаляется целиком (фантом: полигон на суше).
  Фичи без сухих улик не трогаются. 6.22 не изменяется.

Отчёт: класс фичи до/после (phantom/ribbon/clean по dry_frac), сухая
площадь по классам, dry-in-fill (не улика), список удалённых фич.
Выход: results/fusion_6.21/inland_water_6.76.geojson (+ report JSON).
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
from shapely.geometry import shape, box, mapping, MultiPolygon
from shapely.ops import unary_union

REPO = Path('/tmp/my-project/gh-clone')
W22 = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.22.geojson'
WOUT = REPO / 'results' / 'fusion_6.21' / 'inland_water_6.76.geojson'
FUS = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
CONF = '/tmp/my-project/download/mosaic_6.00/spb_terra_conf_6.00_A2.tif'
OUTD = REPO / 'results' / 'water_a3_6.76'
OUTD.mkdir(parents=True, exist_ok=True)

DRY_CM = 150
MIN_COMP_M2 = 25.0      # резать компоненты >= 25 м2 (12+ px); мельче — спекл
MIN_RESIDUAL_M2 = 100.0
TILE_BIG = 0.05
DEG2_M2 = 111320.0 * 111132.0   # м2 в градусe^2
t0 = time.time()
log = lambda m: print(f'[{time.time()-t0:5.0f}s] {m}', flush=True)

feats = json.load(open(W22))['features']
F = rasterio.open(FUS)
C = rasterio.open(CONF)
px_area = abs(F.transform.a) * 111320 * abs(F.transform.e) * 111133
log(f'feats {len(feats)}')


def dry_union_in(g):
    """Сухие компоненты >= MIN_COMP_M2 (полигоны) + счётчики пикселей.
    Куски собираются со всех тайлов БЕЗ порога, объединяются (шов
    склеивается), порог применяется к глобальным компонентам."""
    minx, miny, maxx, maxy = g.bounds
    n_water = n_dry = n_fill = 0
    polys = []
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
            for info, val in rio_shapes(dry.astype(np.uint8), mask=dry,
                                        transform=tr, connectivity=4):
                if val == 1:
                    p = shape(info)
                    if p.area > 0:
                        polys.append(p)
    if not polys:
        return None, n_water, n_dry, n_fill
    u = unary_union(polys)
    pieces = list(u.geoms) if u.geom_type in ('MultiPolygon',
                                              'GeometryCollection') else [u]
    big = [p for p in pieces
           if p.geom_type == 'Polygon' and p.area * DEG2_M2 >= MIN_COMP_M2]
    ev = unary_union(big) if big else None
    return ev, n_water, n_dry, n_fill


def part_dry_frac(p):
    """Доля сухих пикселей (z>=DRY_CM, conf>=10) внутри части p.
    Лечит GEOS-сливеры: часть с dry_frac>=0.5 — не вода."""
    minx, miny, maxx, maxy = p.bounds
    win = from_bounds(minx, miny, maxx, maxy, F.transform)
    win = win.round_offsets().round_lengths()
    x0i, y0i, w_, h_ = int(win.col_off), int(win.row_off), int(win.width), \
        int(win.height)
    if (w_ <= 0 or h_ <= 0 or x0i < 0 or y0i < 0
            or x0i + w_ > F.width or y0i + h_ > F.height):
        return 1.0
    tr = F.window_transform(win)
    pm = rasterize([(p, 1)], out_shape=(h_, w_), transform=tr, fill=0,
                   dtype='uint8') == 1
    if not pm.any():
        return 1.0
    fu = F.read(1, window=Window(x0i, y0i, w_, h_)).astype(np.int32)
    cf = C.read(1, window=Window(x0i, y0i, w_, h_))
    inside = pm & (fu != -32768)
    n = int(inside.sum())
    if n == 0:
        return 1.0
    d = int((inside & (fu >= DRY_CM) & (cf >= 10)).sum())
    return d / n


out_feats, report = [], []
n_drop = n_keep = n_touch = 0
tot = Counter()
for fi, f in enumerate(feats):
    g = shape(f['geometry']).buffer(0)
    ev, n_water, n_dry, n_fill = dry_union_in(g)
    wp_a = n_water * px_area
    dry_a = n_dry * px_area
    fill_a = n_fill * px_area
    frac = dry_a / wp_a if wp_a else 0
    cls = ('empty' if wp_a == 0 else
           'phantom' if frac >= 0.5 else 'ribbon' if frac >= 0.05 else 'clean')
    tot[f'water_before'] += wp_a
    tot[f'dry'] += dry_a
    tot[f'dryfill'] += fill_a
    if ev is None:
        out_feats.append(f)
        n_keep += 1
        report.append({'feat': fi, 'class': cls, 'action': 'keep',
                       'water_km2': round(wp_a / 1e6, 4),
                       'dry_km2': round(dry_a / 1e6, 4),
                       'dryfill_km2': round(fill_a / 1e6, 4)})
        continue
    g2 = g.difference(ev)
    if g2.is_empty:
        n_drop += 1
        tot['dropped_water'] += wp_a
        report.append({'feat': fi, 'class': cls, 'action': 'drop_empty',
                       'water_km2': round(wp_a / 1e6, 4),
                       'dry_km2': round(dry_a / 1e6, 4),
                       'dryfill_km2': round(fill_a / 1e6, 4)})
        continue
    parts = list(g2.geoms) if g2.geom_type == 'MultiPolygon' else [g2]
    parts = [p for p in parts
             if p.area * DEG2_M2 >= MIN_RESIDUAL_M2
             and part_dry_frac(p) < 0.5]
    if not parts:
        n_drop += 1
        tot['dropped_water'] += wp_a
        report.append({'feat': fi, 'class': cls, 'action': 'drop_small',
                       'water_km2': round(wp_a / 1e6, 4),
                       'dry_km2': round(dry_a / 1e6, 4),
                       'dryfill_km2': round(fill_a / 1e6, 4)})
        continue
    g3 = MultiPolygon(parts) if len(parts) > 1 else parts[0]
    n_touch += 1
    tot['carved'] += (g.area - g3.area) * 111320 * 111132
    out_feats.append({'type': 'Feature',
                      'properties': dict(f.get('properties', {}),
                                         fix_stage='6.76_A3'),
                      'geometry': mapping(g3)})
    report.append({'feat': fi, 'class': cls, 'action': 'carve',
                   'water_km2': round(wp_a / 1e6, 4),
                   'dry_km2': round(dry_a / 1e6, 4),
                   'dryfill_km2': round(fill_a / 1e6, 4),
                   'water_after_km2': round(g3.area * 111320 * 111132 / 1e6,
                                            4)})
    if fi % 300 == 0:
        log(f'feat {fi}: kept {n_keep}, carved {n_touch}, dropped {n_drop}')

log(f'kept {n_keep}, carved {n_touch}, dropped {n_drop}')
json.dump({'type': 'FeatureCollection', 'features': out_feats},
          open(WOUT, 'w'))
a0 = sum(shape(f['geometry']).area for f in feats) * 111320 * 111132 / 1e6
a1 = sum(shape(f['geometry']).area for f in out_feats) * 111320 * 111132 / 1e6
log(f'площадь воды: {a0:.2f} -> {a1:.2f} км2 (снято {a0-a1:.2f} км2)')

with open(OUTD / 'fix_report.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=['feat', 'class', 'action',
                                       'water_km2', 'dry_km2',
                                       'dryfill_km2', 'water_after_km2'])
    w.writeheader()
    w.writerows(report)

summary = {
    'date': '2026-11-05', 'stage': '6.76/A3 fix',
    'in': 'inland_water_6.22', 'out': 'inland_water_6.76',
    'rule': 'water -= components of (fusion>=1.5m & conf>=10) with area '
            f'>= {MIN_COMP_M2:.0f} m2; features with residual < '
            f'{MIN_RESIDUAL_M2:.0f} m2 dropped',
    'area_before_km2': round(a0, 2), 'area_after_km2': round(a1, 2),
    'carved_km2': round(a0 - a1, 2),
    'features': {'in': len(feats), 'out': len(out_feats)},
    'actions': dict(Counter(r['action'] for r in report)),
    'classes_in': dict(Counter(r['class'] for r in report)),
    'totals_px_m2': {k: round(v / 1e6, 3) for k, v in tot.items()},
}
(OUTD / 'fix_summary.json').write_text(
    json.dumps(summary, ensure_ascii=False, indent=1), encoding='utf-8')
print(json.dumps({k: summary[k] for k in
                  ('area_before_km2', 'area_after_km2', 'carved_km2',
                   'features', 'actions', 'classes_in')},
                 ensure_ascii=False, indent=1))
log('OK')
