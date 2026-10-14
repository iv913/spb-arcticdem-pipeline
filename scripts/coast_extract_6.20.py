#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.20a — Извлечение региональной воды OSM (water-polygons-split-4326).

Источник: /tmp/waterpoly/water-polygons-split-4326/water_polygons.shp
(глобальные водные полигоны OSM, вкл. океан из natural=coastline; osmdata.openstreetmap.de, ODbL).
Глобальный .shp 1.28 ГБ читается потоково (pyshp), фильтр по bbox СПб,
упрощение 10 м -> results/fusion_6.20/osm_water_6.20.geojson.

Использование: клип зон затопления по суше (береговая линия) в 6.20b.
"""
import json
import os
import time

import shapefile
from shapely.geometry import shape as shp, box as shbox, mapping
from shapely.ops import unary_union

SHP = '/tmp/waterpoly/water-polygons-split-4326/water_polygons.shp'
BBOX = (28.90, 59.45, 31.40, 60.60)          # с запасом вокруг сетки
OUT = '/tmp/my-project/results/fusion_6.20/osm_water_6.20.geojson'


def log(m):
    print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)


def ring_area(pts):
    """Знаковая площадь (шнуровка): CCW > 0, CW < 0."""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


def shape_to_polys(s):
    """Oриентация-независимое вложение: дерево колец по containment,
    чётная глубина = вода (полигон), нечётная = дыра (остров)."""
    from shapely.geometry import Polygon
    pts = s.points
    parts = list(s.parts) + [len(pts)]
    rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
    rings = [rg for rg in rings if len(rg) >= 4]
    polys = [Polygon(rg) for rg in rings]
    # родитель = наименьшее кольцо, содержащее репрезентативную точку
    parent = [None] * len(polys)
    order = sorted(range(len(polys)), key=lambda k: -abs(polys[k].area))
    for idx, k in enumerate(order):
        best, best_area = None, None
        for m in order[:idx]:
            if polys[m].contains(polys[k].representative_point()):
                a = abs(polys[m].area)
                if best is None or a < best_area:
                    best, best_area = m, a
        parent[k] = best
    depth = {}
    for k in order:
        d, p = 0, parent[k]
        while p is not None:
            d += 1
            p = parent[p]
        depth[k] = d
    waters, holes_of = [], {}
    for k in order:
        if depth[k] % 2 == 0:
            waters.append(k)
        else:
            holes_of.setdefault(parent[k], []).append(k)
    out = []
    for k in waters:
        out.append(Polygon(rings[k], [rings[h] for h in holes_of.get(k, [])]))
    return out


def main():
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    bb = shbox(*BBOX)
    r = shapefile.Reader(SHP)
    log(f'записей в shp: {r.numRecords}')
    hits = []
    n = 0
    for i, s in enumerate(r.iterShapes()):
        n += 1
        b = s.bbox                      # [xmin, ymin, xmax, ymax]
        if b[2] < BBOX[0] or b[0] > BBOX[2] or b[3] < BBOX[1] or b[1] > BBOX[3]:
            continue
        for g in shape_to_polys(s):
            gi = g.intersection(bb)
            if gi.is_empty:
                continue
            hits.append(gi)
        if len(hits) % 50 == 0:
            log(f'  хитов: {len(hits)} (просмотрено {n})')
    log(f'просмотрено {n}, хитов {len(hits)}')
    # упрощение 10 м до объединения (скорость/размер)
    simp = [g.simplify(0.0001, preserve_topology=True) for g in hits]
    water = unary_union(simp)
    if water.geom_type == 'Polygon':
        water = [water]
    feats = []
    for g in (water if isinstance(water, list) else list(water.geoms)):
        if g.is_empty or g.geom_type != 'Polygon':
            continue
        feats.append({'type': 'Feature', 'properties': {},
                      'geometry': mapping(g)})
    fc = {'type': 'FeatureCollection', 'name': 'osm_water_6.20',
          'bbox': list(BBOX), 'features': feats}
    with open(OUT, 'w') as f:
        json.dump(fc, f, ensure_ascii=False)
    log(f'{OUT}: {len(feats)} полигонов, {os.path.getsize(OUT)/1e6:.2f} МБ')


if __name__ == '__main__':
    main()
