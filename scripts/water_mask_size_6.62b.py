#!/usr/bin/env python3
"""6.62b: why does the Neva polygon cover land? Size the damage + QA render."""
import json, csv, numpy as np, rasterio, matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from shapely.geometry import shape, Point
import warnings; warnings.filterwarnings('ignore')

WATER = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
BLD   = '/tmp/my-project/gh-clone/results/atayanc_6.30/plan_buildings_6.30.geojson'  # only V.O.; need city OSM buildings
# better: use OSM PBF-derived buildings if present, else sample fusion raster on a grid inside polygon
FUSION = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'

feats = json.load(open(WATER))['features']
g0 = shape(feats[0]['geometry'])
if not g0.is_valid: g0 = g0.buffer(0)
print('feat0: type', feats[0]['geometry']['type'],
      'rings:', len(feats[0]['geometry']['coordinates']),
      'area_km2:', round(g0.area * 111320 * 111132 / 1e6, 2),
      'bounds:', [round(x, 4) for x in g0.bounds])

# grid sample of fusion height inside feat0 (land evidence: fusion >= 1.5 m, solid)
minx, miny, maxx, maxy = g0.bounds
F = rasterio.open(FUSION)
step = 0.0008  # ~65 m
xs = np.arange(minx, maxx, step); ys = np.arange(miny, maxy, step)
land_cells, tot_cells = 0, 0
land_pts = []
for la in ys:
    lon_row = np.full(ys.size, la)  # not used
    los = np.tile(xs, 1)
    las = np.full(xs.size, la)
    v = np.array([x[0] for x in F.sample(list(zip(los, las)))], float)
    v[v == -32768] = np.nan; v *= 0.01
    # test only cells with height (candidate land) — point-in-poly is expensive
    cand = [(lo, la) for lo, val in zip(los, v) if np.isfinite(val) and val >= 2.0]
    tot_cells += xs.size
    if not cand: continue
    from shapely.vectorized import contains as vcontains
    try:
        mask = vcontains(g0, np.array([c[0] for c in cand]), np.array([c[1] for c in cand]))
        land_cells += int(mask.sum())
        land_pts += [c for c, m in zip(cand, mask) if m]
    except Exception:
        for pnt in cand:
            if g0.contains(Point(pnt)):
                land_cells += 1; land_pts.append(pnt)
print(f'grid cells inside feat0 with fusion>=2.0 m: {land_cells} (~{land_cells*0.0064:.1f} km2 land evidence)')

# QA render around the fortress + hits
hits = list(csv.DictReader(open('/tmp/my-project/results/fusion_6.21/water_mask_hits_6.62a.csv')))
fig, axes = plt.subplots(1, 2, figsize=(16, 8))
for ax, (lo0, la0, title) in zip(axes, [(30.316, 59.947, 'Petropavlovskaya fortress'),
                                        (30.283, 59.950, 'm002 area (quay)')]):
    from shapely.geometry import box
    win = box(lo0 - 0.008, la0 - 0.004, lo0 + 0.008, la0 + 0.004)
    poly = g0.intersection(win)
    if not poly.is_empty:
        for geom in (poly.geoms if poly.geom_type == 'MultiPolygon' else [poly]):
            x, y = geom.exterior.xy; ax.fill(x, y, color='#1a4a7a', alpha=0.55)
            for inter in geom.interiors:
                x, y = zip(*inter.xy); ax.plot(x, y, color='#1a4a7a', lw=0.6)
    for h in hits:
        x, y = float(h['lon']), float(h['lat'])
        if win.contains(Point(x, y)):
            ax.plot(x, y, 'o', ms=9, mfc='none', mec='red', mew=1.8)
            ax.annotate(h['id'], (x, y), fontsize=8, color='red')
    ax.set_title(f'feat#0 (Neva) near {title}')
    ax.set_aspect(1 / np.cos(np.radians(59.95))); ax.grid(alpha=0.3)
plt.suptitle('6.62b: inland_water_6.21 feat#0 covers confirmed land (red = confirmed marks)')
plt.tight_layout()
plt.savefig('/tmp/my-project/results/fusion_6.21/qa_water_hits_6.62b.png', dpi=110)
print('qa render saved')
