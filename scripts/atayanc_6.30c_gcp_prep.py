#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""6.30c — дороги/точки OSM для GCP + эталонный рендер OSM ( north-up, WGS84)."""
import json, math, os, time
import osmium
from shapely.geometry import Point, Polygon, mapping

PBF = '/tmp/my-project/data/osm_pbf/sankt_petersburg.osm.pbf'
OUTD = '/tmp/my-project/results/atayanc_6.30'
BBOX = (30.245, 59.928, 30.355, 59.956)

def log(m): print(f'[{time.strftime("%H:%M:%S")}] {m}', flush=True)

roads, pts, special = [], [], {}

class H(osmium.SimpleHandler):
    def way(self, w):
        t = dict(w.tags)
        hw = t.get('highway', '')
        if hw and t.get('name'):
            c = [(n.location.lon, n.location.lat) for n in w.nodes if n.location.valid()]
            if c:
                roads.append({'name': t['name'], 'kind': hw, 'coords': c})
        if t.get('barrier') == 'city_wall' or t.get('historic') or t.get('tourism') or t.get('man_made') in ('obelisk', 'lighthouse'):
            c = [(n.location.lon, n.location.lat) for n in w.nodes if n.location.valid()]
            if c:
                special.setdefault(t.get('name', t.get('historic', t.get('tourism', t.get('man_made')))), []).append(c)
    def node(self, n):
        t = dict(n.tags)
        if n.location.valid() and t.get('name'):
            pts.append({'name': t['name'], 'lon': n.location.lon, 'lat': n.location.lat,
                        'tags': {k: v for k, v in t.items() if k not in ('name',)}})
    def relation(self, r):
        t = dict(r.tags)
        if t.get('name') and any(k in t for k in ('building', 'historic', 'tourism', 'amenity', 'landuse', 'leisure')):
            for m in r.members:
                if m.type == 'n' and m.role in ('', 'main'):
                    pass  # координаты узлов недоступны в relation без locations на member
        # центроиды relation-полигонов не нужны — здания уже есть

log('проход: дороги/точки/спец...')
t0 = time.time()
H().apply_file(PBF, locations=True, idx='flex_mem')
log(f'дорог {len(roads)}, точек {len(pts)}, спец {sum(len(v) for v in special.values())}')

with open(f'{OUTD}/osm_roads_6.30.json', 'w') as f:
    json.dump(roads, f, ensure_ascii=False)
with open(f'{OUTD}/osm_points_6.30.json', 'w') as f:
    json.dump({'nodes': pts, 'special': special}, f, ensure_ascii=False)

# ключевые имена для GCP
want = ['Владимирск', 'Ростральн', 'Кунсткамер', 'Академия художеств', 'Меншиков']
for n in pts:
    if any(w.lower() in n['name'].lower() for w in want):
        log(f"  NODE {n['name']!r} ({n['lon']:.5f},{n['lat']:.5f}) {n['tags']}")
for nm, geoms in special.items():
    nm = nm or ''
    if any(w.lower() in nm.lower() for w in want) or 'Ростральн' in nm:
        for c in geoms:
            cx = sum(p[0] for p in c) / len(c); cy = sum(p[1] for p in c) / len(c)
            log(f"  WAY {nm!r} ({cx:.5f},{cy:.5f}) n={len(c)}")

# ------------------------------------------------ рендер эталона
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.patches import Polygon as MPoly

b = json.load(open(f'{OUTD}/osm_buildings_6.30.geojson'))
water = json.load(open('/tmp/my-project/results/fusion_6.21/inland_water_6.21.geojson'))

fig, ax = plt.subplots(figsize=(18, 12), constrained_layout=True)
ax.set_facecolor('#dfe8d8')
# вода
for f in water['features']:
    for ring in f['geometry']['coordinates']:
        ax.add_patch(MPoly(ring, closed=True, facecolor='#aacbd6', edgecolor='#7fa8ba', lw=0.3, zorder=1))
# здания
for f in b['features']:
    g = f['geometry']['coordinates']
    rings = g if isinstance(g[0][0][0], (int, float)) else g[0]
    ax.add_patch(MPoly(rings[0], closed=True, facecolor='#d9a79c', edgecolor='#a96a58', lw=0.2, zorder=2))
# дороги
lw = {'motorway': 1.6, 'trunk': 1.6, 'primary': 1.3, 'secondary': 1.0, 'tertiary': 0.8, 'residential': 0.45, 'unclassified': 0.4, 'pedestrian': 0.3, 'footway': 0.2, 'service': 0.25}
for r in roads:
    xs = [p[0] for p in r['coords']]; ys = [p[1] for p in r['coords']]
    ax.plot(xs, ys, color='white', lw=lw.get(r['kind'], 0.4), zorder=3, solid_capstyle='round')
# подписи ключевых улиц
seen = set()
for r in roads:
    if r['name'] in seen or r['kind'] not in ('primary', 'secondary', 'tertiary', 'residential'):
        continue
    if len(r['name']) < 3 or len(r['coords']) < 2:
        continue
    seen.add(r['name'])
    p = r['coords'][len(r['coords']) // 2]
    ax.text(p[0], p[1], r['name'], fontsize=5.5, color='#333333', zorder=4, ha='center')
ax.set_xlim(BBOX[0], BBOX[2]); ax.set_ylim(BBOX[1], BBOX[3])
ax.set_aspect(1 / math.cos(math.radians(59.94)))
ax.set_xticks([30.25 + 0.01 * i for i in range(11)])
ax.set_yticks([59.93 + 0.002 * i for i in range(14)])
ax.grid(color='#c9c9c9', lw=0.3, alpha=0.6)
ax.tick_params(labelsize=7)
ax.set_title('OSM reference (north-up) — bbox 30.245-30.355 / 59.928-59.956', fontsize=10)
fig.savefig(f'{OUTD}/osm_reference_6.30.png', dpi=170)
log('рендер готов: osm_reference_6.30.png')
