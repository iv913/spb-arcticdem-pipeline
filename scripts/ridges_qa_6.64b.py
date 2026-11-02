#!/usr/bin/env python3
"""6.64b: QA of ridge catalog v0 + granite-vs-pavement dH split.

METHOD_temporal_epochs 6: marks <=1990 on granite (stable, r~0) vs pavement
(r~0.01 m/yr). Split by joining pool marks to barrier=wall/retaining_wall
lines (30 m): 'granite' subset vs the rest.
"""
import json, csv, numpy as np, rasterio, warnings, matplotlib
warnings.filterwarnings('ignore')
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import shape, Point
from shapely.strtree import STRtree

OUTD = '/tmp/my-project/results/ridges_6.64'
RIDGE = f'{OUTD}/ridge_lines_v0.geojson'
POOL2 = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
FUSION = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'

F = rasterio.open(FUSION)
def fus(lo, la):
    v = np.array([x[0] for x in F.sample(list(zip(lo, la)))], float)
    v[v == -32768] = np.nan
    return v * 0.01

rf = json.load(open(RIDGE))['features']
barrier_geoms, barrier_meta = [], []
for f in rf:
    if f['properties']['kind'] not in ('barrier', 'quay_edge'): continue
    g = shape(f['geometry'])
    barrier_geoms.append(g)
    barrier_meta.append(f['properties'])
tree = STRtree(barrier_geoms)

pool = [r for r in csv.DictReader(open(POOL2))]
res = []
for r in pool:
    h = r.get('h_best') or ''
    if not h: continue
    p = Point(float(r['lon']), float(r['lat']))
    idxs = tree.query(p.buffer(30 / 111320 / np.cos(np.radians(p.y))))
    on_wall = any(barrier_geoms[i].distance(p) * 111320 * np.cos(np.radians(p.y)) <= 30
                  for i in idxs)
    fm = fus([p.x], [p.y])[0]
    if not np.isfinite(fm): continue
    res.append({'id': r['id'], 'use': r['use'], 'on_wall': on_wall,
                'h': float(h), 'fusion': float(fm), 'dH': float(fm) - float(h),
                'conf': r.get('conf_claude', '')})

conf = [x for x in res if x['use'] in ('ground_truth', 'ground_truth_0.1')]
granite = [x for x in conf if x['on_wall']]
pavement = [x for x in conf if not x['on_wall']]
med = lambda v: float(np.median(v)) if v else float('nan')
print(f"confirmed marks: {len(conf)}")
print(f"  on granite walls (<=30 m from barrier): {len(granite)}, median dH {med([x['dH'] for x in granite]):+.2f}")
print(f"  elsewhere (pavement/streets):           {len(pavement)}, median dH {med([x['dH'] for x in pavement]):+.2f}")
allr = [x for x in res]
print(f"all single+confirmed: {len(allr)}, on_wall {sum(x['on_wall'] for x in allr)}, "
      f"median dH granite {med([x['dH'] for x in allr if x['on_wall']]):+.2f} vs off {med([x['dH'] for x in allr if not x['on_wall']]):+.2f}")

with open(f'{OUTD}/granite_split_6.64.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=['id', 'use', 'on_wall', 'h', 'fusion', 'dH', 'conf'])
    w.writeheader(); w.writerows(res)

# QA render: ridges with heights, center window
fig, ax = plt.subplots(figsize=(15, 11))
lo0, lo1, la0, la1 = 30.20, 30.42, 59.925, 59.985
for f in rf:
    g = shape(f['geometry'])
    if not g.intersects(plt_bbox := __import__('shapely.geometry', fromlist=['box']).box(lo0, la0, lo1, la1)):
        continue
    hp = f['properties'].get('h_crest_m', '')
    x, y = g.xy
    if hp != '':
        ax.plot(list(x), list(y), color='#e07b39', lw=1.4)
    else:
        ax.plot(list(x), list(y), color='#4a6fa5', lw=0.35, alpha=0.5)
sc = ax.scatter([float(f['lon']) for f in []], [])
import csv as _csv
pool_pts = [(float(r['lon']), float(r['lat']), float(r['h_best']))
            for r in pool if r.get('h_best') and lo0 < float(r['lon']) < lo1 and la0 < float(r['lat']) < la1]
ax.scatter([p[0] for p in pool_pts], [p[1] for p in pool_pts],
           c=[p[2] for p in pool_pts], cmap='viridis', s=14, zorder=5)
plt.colorbar(ax.collections[0], ax=ax, label='mark height, m BS-77')
ax.set_title('6.64: ridge skeleton v0 — orange: crest height known (352), blue: geometry only; dots: marks')
ax.set_aspect(1 / np.cos(np.radians(59.95))); ax.grid(alpha=0.3)
plt.tight_layout(); plt.savefig(f'{OUTD}/qa_ridges_v0_center.png', dpi=105)
print('render OK -> qa_ridges_v0_center.png')
