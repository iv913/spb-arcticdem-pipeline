#!/usr/bin/env python3
"""6.76/A3: влияние на городскую карту — зоны 5.96_clip2 по воде 6.22 vs 6.76.

Метод 6.62e: каждая зона уровней clip2 клипится маской; сравнение площадей.
Ожидание: фантомы сидели на высоком берегу (z>5 м) — на зоны +1..+5 почти
не влияют; реальные изъятия (порт, русла, стенки) могут вернуть немного суши.
"""
import json
from collections import defaultdict
from pathlib import Path

from shapely.geometry import shape
from shapely.ops import unary_union

REPO = Path('/tmp/my-project/gh-clone')
FUS = REPO / 'results' / 'fusion_6.21'
Z = json.load(open(FUS / 'flood_levels_5.96_clip2.geojson'))['features']
W22 = unary_union([shape(f['geometry']).buffer(0) for f in
                   json.load(open(FUS / 'inland_water_6.22.geojson'))['features']])
W76 = unary_union([shape(f['geometry']).buffer(0) for f in
                   json.load(open(FUS / 'inland_water_6.76.geojson'))['features']])

area_by = defaultdict(lambda: [0.0, 0.0])
for f in Z:
    lv = f.get('properties', {}).get('level_m',
                                     f.get('properties', {}).get('level', '?'))
    g = shape(f['geometry']).buffer(0)
    a22 = g.difference(W22).area * 111320 * 111132 / 1e6
    a76 = g.difference(W76).area * 111320 * 111132 / 1e6
    area_by[lv][0] += a22
    area_by[lv][1] += a76

print(f"{'level_m':>8s} {'clip 6.22 км2':>15s} {'clip 6.76 км2':>15s} "
      f"{'вернулось км2':>14s}")
tot = [0.0, 0.0]
for lv in sorted(area_by, key=lambda x: float(x) if
                 str(x).replace('.', '').isdigit() else 0):
    a, b = area_by[lv]
    tot[0] += a
    tot[1] += b
    print(f"{lv!s:>8s} {a:15.3f} {b:15.3f} {b-a:14.3f}")
print(f"{'TOTAL':>8s} {tot[0]:15.3f} {tot[1]:15.3f} {tot[1]-tot[0]:14.3f}")
