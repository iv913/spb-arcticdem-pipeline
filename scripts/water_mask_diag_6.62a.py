#!/usr/bin/env python3
"""6.62a: diagnose inland_water_6.21 polygons that cover confirmed land marks.

Audit finding: 13 marks of the Atayans pool (h 2.7-3.4, fusion 2.0-3.4 = land)
sit inside "water", incl. 3 inside Petropavlovsk fortress (30.324, 59.951-59.952).
There the flood map clips flooding as "river" -> false dry (or wrong clip).
"""
import json, numpy as np, rasterio
from shapely.geometry import shape, Point
from shapely.ops import unary_union
import warnings; warnings.filterwarnings('ignore')

WATER = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
POOL2 = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
FUSION = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'

F = rasterio.open(FUSION)
def fus(lo, la):
    v = np.array([x[0] for x in F.sample(list(zip(lo, la)))], float)
    v[v == -32768] = np.nan
    return v * 0.01

feats = json.load(open(WATER))['features']
geoms = []
for i, f in enumerate(feats):
    g = shape(f['geometry'])
    if not g.is_valid: g = g.buffer(0)
    geoms.append((i, g))

pool = [r for r in csv_iter()] if False else None
import csv
pool = list(csv.DictReader(open(POOL2)))
conf = [r for r in pool if r['use'] in ('ground_truth', 'ground_truth_0.1')]
print(f'water feats: {len(feats)}, confirmed marks: {len(conf)}')

hits = []
for r in conf:
    p = Point(float(r['lon']), float(r['lat']))
    fm = fus([p.x], [p.y])[0]
    inside = [(i, g) for i, g in geoms if g.contains(p)]
    if inside:
        hits.append((r, p, fm, inside))
        for i, g in inside:
            prop = feats[i].get('properties', {})
            print(f"  {r['id']} use={r['use']} h={r['h_best']} fusion={fm:.2f} -> feat#{i} "
                  f"area_km2={g.area*111320*111132/1e6:.3f} src={prop.get('src','?')} "
                  f"name={prop.get('name','')!r}")
print(f'\ntotal confirmed marks inside water: {len(hits)}')

# cluster the offending features
from collections import Counter
c = Counter(i for _, _, _, ins in hits for i, _ in ins)
print('offending feature ids:', c.most_common())

# save hit list
with open('/tmp/my-project/results/fusion_6.21/water_mask_hits_6.62a.csv', 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['id', 'lon', 'lat', 'h_best', 'fusion_m', 'feat_ids'])
    for r, p, fm, ins in hits:
        w.writerow([r['id'], p.x, p.y, r['h_best'], f'{fm:.2f}', ';'.join(str(i) for i, _ in ins)])
print('saved hits csv')
