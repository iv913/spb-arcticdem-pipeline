#!/usr/bin/env python3
"""6.64a: ridge skeleton v0 — Stage 1 of REVIEW_flood_surface_pipeline.

Geometry: OSM PBF lines/polygons of crest-carrying structures:
  barrier=wall|retaining_wall, man_made=embankment|dyke|breakwater|groyne|pier,
  embankment=yes (lines), man_made=quay (polygons -> boundary lines).
Heights: join marks (pool v2 confirmed weighted + passport + catalog) within
  30 m of a ridge line -> h_crest, source, weight. Strip profiles: TODO 6.65.
Output: results/ridges_6.64/ridge_lines_v0.geojson + join report.
"""
import json, csv, osmium, time, warnings, numpy as np
warnings.filterwarnings('ignore')
from shapely.geometry import shape, Point, LineString, Polygon, MultiPolygon, mapping
from shapely.strtree import STRtree
from shapely.ops import unary_union

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
POOL2 = '/tmp/my-project/gh-clone/results/atayanc_pool_v2/atayanc_marks_pool_v2.csv'
PASSPORT = '/tmp/my-project/gh-clone/scripts/data/spb_passport_validation_v11.csv'
ALLIND = '/tmp/my-project/gh-clone/scripts/data/ALL_independent_points.csv'
OUTD = '/tmp/my-project/results/ridges_6.64'
import os; os.makedirs(OUTD, exist_ok=True)

t0 = time.time(); log = lambda m: print(f'[{time.time()-t0:5.0f}s] {m}', flush=True)

def is_ridge(t):
    if t.get('barrier') in ('wall', 'retaining_wall'): return 'barrier'
    if t.get('man_made') in ('embankment', 'dyke', 'breakwater', 'groyne'): return 'man_made'
    if t.get('embankment') == 'yes': return 'embankment'
    if t.get('man_made') == 'quay': return 'quay'
    if t.get('waterway') == 'canal' and t.get('tunnel') in ('culvert', 'yes'): return 'culvert'
    if t.get('bridge') and t.get('bridge') not in ('no',): return 'bridge'
    return None

class Ridges(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.lines = []
    def way(self, w):
        k = is_ridge(w.tags)
        if not k: return
        try:
            cs = [(n.lon, n.lat) for n in w.nodes]
        except Exception:
            return
        if len(cs) < 2: return
        name = w.tags.get('name', '')
        self.lines.append({'kind': k, 'name': name, 'geom': LineString(cs)})

R = Ridges(); R.apply_file(PBF, locations=True, idx='flex_mem')
log(f'ridge ways: {len(R.lines)} '
    + str({k: sum(1 for l in R.lines if l["kind"] == k) for k in set(l["kind"] for l in R.lines)}))

# quay polygons -> boundary ring lines
class Quays(osmium.SimpleHandler):
    def __init__(self):
        super().__init__(); self.rings = []
    def way(self, w):
        if w.tags.get('man_made') != 'quay': return
        try:
            cs = [(n.lon, n.lat) for n in w.nodes]
        except Exception:
            return
        if len(cs) >= 4:
            p = Polygon(cs)
            if p.is_valid and p.area > 0:
                self.rings.append({'kind': 'quay_edge', 'name': w.tags.get('name', ''),
                                   'geom': LineString(p.exterior.coords)})
Q = Quays(); Q.apply_file(PBF, locations=True, idx='flex_mem')
log(f'quay-edge rings: {len(Q.rings)}')
R.lines += Q.rings

# ---------- marks ----------
marks = []
W = {'confirmed': 1.0, 'near': 0.9, 'conflict': 0.3, 'new_single_read': 0.3, 'agent_only': 0.3}
for r in csv.DictReader(open(POOL2)):
    h = r.get('h_best') or ''
    if not h: continue
    w = W.get(r['status'], 0.3)
    if r.get('flag_vs_neighbours') == '1': w *= 0.5
    marks.append({'id': 'at2_' + r['id'], 'lon': float(r['lon']), 'lat': float(r['lat']),
                  'h': float(h), 'w': w, 'src': 'atayans_v2/' + r['status']})
for r in csv.DictReader(open(PASSPORT)):
    marks.append({'id': 'pp_' + r['key'][:14], 'lon': float(r['lon']), 'lat': float(r['lat']),
                  'h': float(r['hp']), 'w': {'21': 1.0, '23': 0.5, '24': 0.7}.get(r['detail'], 0.3),
                  'src': f"passport/d{r['detail']}"})
for r in csv.DictReader(open(ALLIND)):
    if r['source'] == 'archi.ru 514913':
        marks.append({'id': 'ku_' + r['lat'][:8], 'lon': float(r['lon']), 'lat': float(r['lat']),
                      'h': float(r['z_survey']), 'w': 1.0, 'src': 'archi_514913'})
log(f'marks: {len(marks)}')

# ---------- join marks to ridges within 30 m ----------
geoms = [l['geom'] for l in R.lines]
tree = STRtree(geoms)
JOIN_M = 30.0
DEG_M = 111320.0
joined = 0
for i, l in enumerate(R.lines):
    best = None
    for m in marks:
        d = l['geom'].distance(Point(m['lon'], m['lat'])) * DEG_M * np.cos(np.radians(m['lat']))
        if d <= JOIN_M and (best is None or d < best[0]):
            best = (d, m)
    if best:
        d, m = best
        l['h_crest'] = m['h']; l['h_src'] = m['src']; l['h_w'] = m['w']
        l['h_dist_m'] = round(d, 1)
        joined += 1
log(f'ridge lines with crest height: {joined} / {len(R.lines)}')

# ---------- save ----------
feats = []
for l in R.lines:
    f = {'type': 'Feature',
         'properties': {'kind': l['kind'], 'name': l['name'],
                        'h_crest_m': l.get('h_crest', ''), 'h_src': l.get('h_src', ''),
                        'h_w': l.get('h_w', ''), 'h_dist_m': l.get('h_dist_m', ''),
                        'stage': '6.64'},
         'geometry': mapping(l['geom'])}
    feats.append(f)
json.dump({'type': 'FeatureCollection', 'features': feats},
          open(f'{OUTD}/ridge_lines_v0.geojson', 'w'), ensure_ascii=False)
from collections import Counter
withh = [f for f in feats if f['properties']['h_crest_m'] != '']
log(f"saved ridge_lines_v0.geojson: {len(feats)} lines, {len(withh)} with crest height")
log('kinds with height: ' + str(Counter(f['properties']['kind'] for f in withh)))
