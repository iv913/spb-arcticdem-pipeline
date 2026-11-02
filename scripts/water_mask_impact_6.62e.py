#!/usr/bin/env python3
"""6.62e: impact on the city flood clip — zones 6.21 clipped by water 6.21 vs 6.22."""
import json, numpy as np, warnings
warnings.filterwarnings('ignore')
from shapely.geometry import shape
from shapely.ops import unary_union

FUS = '/tmp/my-project/results/fusion_6.21'
Z = json.load(open(f'{FUS}/flood_levels_5.96_clip2.geojson'))['features']
W21 = unary_union([shape(f['geometry']).buffer(0) for f in
                   json.load(open(f'{FUS}/inland_water_6.21.geojson'))['features']])
W22 = unary_union([shape(f['geometry']).buffer(0) for f in
                   json.load(open(f'{FUS}/inland_water_6.22.geojson'))['features']])

from collections import defaultdict
area_by = defaultdict(lambda: [0.0, 0.0])
for f in Z:
    lv = f.get('properties', {}).get('level_m', f.get('properties', {}).get('level', '?'))
    g = shape(f['geometry']).buffer(0)
    # per-feature area after clipping with each water mask
    a21 = g.difference(W21).area * 111320 * 111132 / 1e6
    a22 = g.difference(W22).area * 111320 * 111132 / 1e6
    area_by[lv][0] += a21; area_by[lv][1] += a22

print(f"{'level_m':>8s} {'clip by 6.21 km2':>17s} {'clip by 6.22 km2':>17s} {'returned km2':>13s}")
tot = [0.0, 0.0]
for lv in sorted(area_by, key=lambda x: float(x) if str(x).replace('.','').isdigit() else 0):
    a, b = area_by[lv]; tot[0] += a; tot[1] += b
    print(f"{lv!s:>8s} {a:17.3f} {b:17.3f} {b-a:13.3f}")
print(f"{'TOTAL':>8s} {tot[0]:17.3f} {tot[1]:17.3f} {tot[1]-tot[0]:13.3f}")
