#!/usr/bin/env python3
"""6.62d: acceptance check of inland_water_6.22 + before/after render."""
import json, csv, numpy as np, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import shape, Point, box
import warnings; warnings.filterwarnings('ignore')

W21 = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
W22 = '/tmp/my-project/results/fusion_6.21/inland_water_6.22.geojson'
POOL2 = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
CAT = '/tmp/my-project/gh-clone/results/atayanc_6.50/marks_catalog_6.50.csv'

g21 = [shape(f['geometry']).buffer(0) for f in json.load(open(W21))['features']]
g22 = [shape(f['geometry']).buffer(0) for f in json.load(open(W22))['features']]
u21 = g21[0]; u22 = g22[0]

# 1. marks inside water: before vs after (full catalog 332)
rows = list(csv.DictReader(open(CAT)))
before = after = conf_before = conf_after = 0
pool = {r['id']: r for r in csv.DictReader(open(POOL2))}
conf_ids = {k for k, v in pool.items() if v['use'] in ('ground_truth', 'ground_truth_0.1')}
for r in rows:
    p = Point(float(r['lon']), float(r['lat']))
    b = any(g.contains(p) for g in g21); a = any(g.contains(p) for g in g22)
    before += b; after += a
    if r['id'] in conf_ids:
        conf_before += b; conf_after += a
print(f'catalog marks inside water: {before} -> {after}')
print(f'confirmed-188 marks inside water: {conf_before} -> {conf_after}')

# 2. render before/after at fortress and Strelka
fig, axes = plt.subplots(2, 2, figsize=(17, 11))
views = [('Petropavlovskaya fortress', 30.319, 59.948),
         ('Strelka V.O. / Birzhevaya', 30.295, 59.944)]
hits = {r['id'] for r in rows if r['id'] in conf_ids}
for col, (title, lo0, la0) in enumerate(views):
    for row, (gs, lbl) in enumerate([(g21, 'BEFORE 6.21'), (g22, 'AFTER 6.22')]):
        ax = axes[row][col]
        win = box(lo0 - 0.010, la0 - 0.005, lo0 + 0.010, la0 + 0.005)
        for g in gs:
            if not g.intersects(win): continue
            part = g.intersection(win)
            for geom in (part.geoms if part.geom_type in ('MultiPolygon', 'GeometryCollection')
                         else [part]):
                if geom.geom_type != 'Polygon': continue
                x, y = geom.exterior.xy; ax.fill(x, y, color='#3b6ea5', alpha=0.65)
                for inter in geom.interiors:
                    xs_i, ys_i = inter.xy
                    ax.plot(list(xs_i), list(ys_i), color='#3b6ea5', lw=0.6)
        for r in rows:
            x, y = float(r['lon']), float(r['lat'])
            if win.contains(Point(x, y)) and r['id'] in hits:
                ax.plot(x, y, 'o', ms=8, mfc='none', mec='red', mew=1.6)
        ax.set_title(f'{lbl}: {title}'); ax.set_aspect(1 / np.cos(np.radians(59.95)))
        ax.grid(alpha=0.3, lw=0.4)
plt.suptitle('6.62: inland water mask — confirmed land marks (red) and evidence returned to land')
plt.tight_layout()
plt.savefig('/tmp/my-project/results/fusion_6.21/qa_water_622_before_after.png', dpi=105)
print('render OK')
