#!/usr/bin/env python3
"""6.62c: fix inland_water_6.21 -> inland_water_6.22.

Land evidence subtracted from water polygons (OSM PBF 10.10.2026):
  (a) buildings (ways + relations), buffered +3 m;
  (b) place=island, man_made=quay, leisure=park/garden, landuse
      grass/cemetery/recreation_ground, natural=wood/scrub, buffered +2 m;
  (c) discs r=40 m around marks where BOTH mark height (>= 2.0 m) and the
      fusion surface (>= 1.5 m) say land (pool v2 confirmed + full catalog).
Water polygons without evidence stay untouched. 6.21 file is kept as is.
"""
import json, csv, osmium, numpy as np, rasterio, time, warnings
warnings.filterwarnings('ignore')
from shapely.geometry import shape, mapping, Point, Polygon, MultiPolygon
from shapely.ops import unary_union
from shapely.vectorized import contains as vcontains

WATER_IN  = '/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'
WATER_OUT = '/tmp/my-project/results/fusion_6.21/inland_water_6.22.geojson'
PBF   = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
POOL2 = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
CAT   = '/tmp/my-project/gh-clone/results/atayanc_6.50/marks_catalog_6.50.csv'
FUSION = '/tmp/my-project/download/mosaic_6.00/spb_terra_fusion_6.00_A2.tif'
OUTD  = '/tmp/my-project/results/fusion_6.21'

t0 = time.time(); log = lambda m: print(f'[{time.time()-t0:5.0f}s] {m}', flush=True)

feats = json.load(open(WATER_IN))['features']
geoms = []
for f in feats:
    g = shape(f['geometry'])
    if not g.is_valid: g = g.buffer(0)
    geoms.append(g)
feat0 = geoms[0]
minx, miny, maxx, maxy = feat0.bounds
log(f'water feats: {len(feats)}; feat0 area {feat0.area*111320*111132/1e6:.1f} km2')

# ---------- 1. land evidence from PBF ----------
LAND_WAY = lambda t: (('building' in t and t['building'] not in ('no', 'roof'))
                      or t.get('place') == 'island' or t.get('man_made') == 'quay'
                      or t.get('leisure') in ('park', 'garden')
                      or t.get('landuse') in ('grass', 'cemetery', 'recreation_ground')
                      or t.get('natural') in ('wood', 'scrub'))

class Land(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.polys = []; self.rels = []
    def way(self, w):
        t = w.tags
        if not LAND_WAY(t): return
        try:
            coords = [(n.lon, n.lat) for n in w.nodes]
        except Exception:
            return
        if len(coords) >= 4:
            self.polys.append(Polygon(coords))
    def relation(self, r):
        t = r.tags
        if not (t.get('place') == 'island'
                or ('building' in t and t['building'] not in ('no', 'roof'))):
            return
        outer = []
        for m in r.members:
            if m.type == 'w' and m.role in ('outer', ''):
                try:
                    coords = [(n.lon, n.lat) for n in m.way.nodes]
                except Exception:
                    continue
                if len(coords) >= 4:
                    outer.append(Polygon(coords))
        if outer:
            u = unary_union(outer)
            self.rels.append(max(u.geoms, key=lambda g: g.area)
                             if u.geom_type == 'MultiPolygon' else u)
L = Land(); L.apply_file(PBF, locations=True, idx='flex_mem')
L.polys += L.rels
log(f'city land-evidence polys: {len(L.polys)}')

cand = [p for p in L.polys if p.bounds[0] < maxx and p.bounds[2] > minx
        and p.bounds[1] < maxy and p.bounds[3] > miny]
log(f'land evidence in bbox: {len(cand)}')
cand = [p for p in cand if p.is_valid or p.buffer(0).area > 0]
cand = [p.buffer(0) if not p.is_valid else p for p in cand]
midx = np.array([(p.centroid.x, p.centroid.y) for p in cand])
mask = vcontains(feat0, midx[:, 0], midx[:, 1])
inside = [p for p, m in zip(cand, mask) if m]
# also polys that INTERSECT feat0 but centroid outside (island bitten by ring)
extra = [p for p, m in zip(cand, mask) if not m and p.intersects(feat0)
         and p.intersection(feat0).area > 0.3 * p.area]
inside += extra
a_in = sum(p.area for p in inside) * 111320 * 111132 / 1e6
log(f'land evidence INSIDE feat0: {len(inside)} polys, {a_in:.2f} km2')

# ---------- 2. mark discs ----------
F = rasterio.open(FUSION)
def fus(lo, la):
    v = np.array([x[0] for x in F.sample(list(zip(lo, la)))], float)
    v[v == -32768] = np.nan
    return v * 0.01

disc_polys, mark_rows, seen = [], [], set()
for path, is_conf in ((POOL2, True), (CAT, False)):
    for r in csv.DictReader(open(path)):
        lo, la = float(r['lon']), float(r['lat'])
        key = (round(lo, 6), round(la, 6))
        if key in seen: continue
        h = float(r.get('h_best') or r.get('h_bs77_m') or 0)
        if h < 2.0: continue
        if not feat0.contains(Point(lo, la)): continue
        # confirmed double-read marks are land evidence on their own (granite quays:
        # fusion 2 m ramps down to water level at the wall, 6.51); single reads
        # need the surface to agree.
        if is_conf:
            ok_land = r['use'] in ('ground_truth', 'ground_truth_0.1')
        else:
            fm0 = fus([lo], [la])[0]
            ok_land = np.isfinite(fm0) and fm0 >= 1.5
        if not ok_land: continue
        seen.add(key)
        disc_polys.append(Point(lo, la).buffer(40 / 111320 / np.cos(np.radians(la))))
        mark_rows.append({'id': r['id'], 'lon': lo, 'lat': la, 'h': h,
                          'fusion': round(float(fus([lo], [la])[0]), 2)})
log(f'mark discs: {len(disc_polys)}')

# ---------- 3. evidence union, subtract ----------
parts = []
if inside:
    parts += [p.buffer(3 / 111320) for p in inside]
parts += disc_polys
EV = unary_union(parts) if parts else None
if EV is not None:
    log(f'evidence area: {EV.area*111320*111132/1e6:.2f} km2')

out_feats, changed = [], 0
for g, f in zip(geoms, feats):
    g2 = g.difference(EV) if EV is not None else g
    if g2.is_empty:
        changed += 1; continue
    if g2.geom_type == 'MultiPolygon':
        keep = [p for p in g2.geoms if p.area > 1e-9]
        g2 = MultiPolygon(keep) if len(keep) > 1 else keep[0]
    if not g2.equals(g): changed += 1
    out_feats.append({'type': 'Feature',
                      'properties': dict(f.get('properties', {}), fix_stage='6.62'),
                      'geometry': mapping(g2)})
log(f'features changed: {changed} / {len(geoms)}')

json.dump({'type': 'FeatureCollection', 'features': out_feats}, open(WATER_OUT, 'w'))
a0 = sum(g.area for g in geoms) * 111320 * 111132 / 1e6
a1 = sum(shape(f['geometry']).area for f in out_feats) * 111320 * 111132 / 1e6
log(f'water area: {a0:.2f} -> {a1:.2f} km2 (land returned: {a0-a1:.2f} km2)')

with open(f'{OUTD}/mark_discs_6.62.csv', 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['id', 'lon', 'lat', 'h', 'fusion'])
    for r in mark_rows: w.writerow([r['id'], r['lon'], r['lat'], r['h'], r['fusion']])
log('OK -> inland_water_6.22.geojson')
